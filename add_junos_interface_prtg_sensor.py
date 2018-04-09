import requests
import socket
import json
import csv
from jnpr.junos import Device
import jnpr.junos
#for user prompt to enter passwords 
from getpass import getpass
#used to parse the xml???
from lxml import etree
import sys
#from myTables.ConfigTables import InterfaceTable
from jnpr.junos.factory.factory_loader import FactoryLoader
import yaml
import time
#multiprocesssing stuff
from multiprocessing import Pool
from multiprocessing.dummy import Pool as ThreadPool 
from functools import partial
#disable https verify failure warning
from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning) 

#this YAML data should be stored in a separate file and called properly (see https://github.com/Juniper/py-junos-eznc/tree/master/lib/jnpr/junos/op for examples how, and https://www.juniper.net/documentation/en_US/junos-pyez1.2/topics/task/program/junos-pyez-views-op-defining.html)
# but I'm lazy so just putting it here
# YAML data is used to access the different "tables" or "views" in Junos (which really are just the different sections)
#xpath used for the items and fields
#table is the rpc callable
#view is the fields you want within the rpc/table
yaml_data="""
---
PhysicalInterfaceTable:
  rpc: get-interface-information
  item: physical-interface
  key: name
  view: InterfaceView
  
InterfaceView:
  fields:
    name: name
    description: description
    snmp_index: snmp-index

LogicalInterfaceTable:
  rpc: get-interface-information
  item: physical-interface/logical-interface
  key: name
  view: InterfaceView
  
InterfaceView:
  fields:
    name: name
    description: description
    snmp_index: snmp-index
"""
#This is needed to load the YAML 
globals().update(FactoryLoader().load(yaml.load(yaml_data)))
# auth details for PRTG API calls
myuser = ''
mypassword = ''

def check_interfaces(tablename,dev, input_interface_description):
#get interface info using custom table defined above in YAML section, and passed from get_interfaces() function
	interface = tablename(dev)
	interface.get()
	#loop through ALL interfaces on router that match the relevant tablename,  look for ones that contain the user input, and add them to a list
	for interface in interface:
		#convert to lower to match all cases
		if input_interface_description.lower() in str(interface.description).lower():
			#store facts 
			devicefacts = dev.facts
			#get hostname
			hostname = devicefacts['hostname'].replace("'","")
			print hostname
			print 'Interface Name: ', interface.name
			print 'SNMP Index: ', interface.snmp_index
			print 'Description: ', str(interface.description)
			print '----------'
			results = hostname, interface.name, interface.snmp_index, str(interface.description)
			interface_list.append(results)
				
#function to connect to devices, and then run the check_interface function)			
def get_data(ipaddress, username, passwd,input_interface_description):
	#check to see if netconf is reachable, otherwise timeout after the value (in seconds)
	Device.auto_probe = 1
	#create device object
	dev = Device(host=ipaddress, user=username, password=passwd, port = "22" )
	#connet to device
	try:
		dev.open()
		print("\nNETCONF connection to %s opened!" %ipaddress)
		print("Beginning data collection...\n")
		#collect data
		check_interfaces(PhysicalInterfaceTable, dev, input_interface_description)
		check_interfaces(LogicalInterfaceTable, dev, input_interface_description)
		print("\nOperation complete.")
		dev.close()
		print("NETCONF connection to %s is now closed.\n" %ipaddress)
	#if probe times out and raises a probe error
	except jnpr.junos.exception.ProbeError as e: 
		print("NETCONF connection to %s is not reachable, moving on.." %ipaddress)
	#any other error
	except Exception as e:
		print (e)

def add_prtg_sensor(prestage_data_id):
	print "Adding option", prestage_data_id
	prestage_data_id = prestage_data_id - 1
	#pull required details from list and assign to variables
	PRTGObjID = str(sensor_prestage_data[prestage_data_id]['PRTGObjID'])
	intSNMPid = str(sensor_prestage_data[prestage_data_id]['intSNMPid'])
	ServiceName = str(sensor_prestage_data[prestage_data_id]['ServiceName'])
	#call to duplicate the template SNMP traffic sensor, which then returns the URL of the new sensor. target ID is the ID of the device object in prtg to create the new sensor on
	r = requests.get('https://prtg.cc.lan//api/duplicateobject.htm?id=11383&name=' + ServiceName + '&targetid=' + PRTGObjID + '&username='+ myuser + '&password=' + mypassword, verify=False)
	print "HTTPS Status Code: ", r.status_code
	#extract new prtg object id from returned url
	new_object_id = str(r.url.strip('https://prtg.cc.lan/sensor.htm?id='))
	print "New PRTG object ID is:", new_object_id
	#api call to change interface id on prtg
	r = requests.get('https://prtg.cc.lan/api/setobjectproperty.htm?id='+ new_object_id + '&name=interfacenumber&value=' + intSNMPid + '&username='+ myuser + '&password=' + mypassword, verify=False)
		
#user inputs
user = raw_input("Username: ")	
passwd = getpass("Device password: ")
service_to_search_for = raw_input("Service Name: ")	

#Get a list of all devices in PRTG "Juniper Routers" group and return their name and object ID (basis for a menu system)
#send request to prtg api, verify needed to ignore https cert warnings
r = requests.get('https://prtg.cc.lan/api/table.json?id=2114&content=devices&output=json&columns=objid,device&&username='+ myuser + '&password=' + mypassword, verify=False)
print "HTTPS Status Code: ", r.status_code
#load response into json
devices = r.json()
#load the actual device object data
devices = devices["devices"]

#all device management ips, read from list
with open('Device IP List.csv', 'rb') as ipfile:
	reader = csv.reader(ipfile)
	ip_list = list(reader)
	
#create list to store results
interface_list = []
router_list = []
#go through the list loaded in the file above, and pull out the IP addresses only
for router in ip_list:
	router_list.append(router[1])

#run the search function through all the ips in the list, and if they have a valid result (ie doesn't return None type) store in list
#start timer for collection stats
start_time = time.time()

#parallel processing of data collection!!! 
#set number of threads
pool = ThreadPool(15) # Sets the pool size to max number of cores of the machine, can also explicitly state number if required
#partial function used because only the first argument of get_data() (ie, the ip address) is a variable, everything else is a constant
#have to then explicitly state that the other arguments are constants, see here http://spencerimp.blogspot.nl/2015/12/python-multiprocess-with-multiple.html
get_data_partialized = partial(get_data, username = user, passwd = passwd,input_interface_description=service_to_search_for)
#run the parallel processing using the partalized data above, and the ip's from the router lis to iterate through
pool.map(get_data_partialized, router_list)
#housekeeping, close all pools
pool.close()
pool.join()

#create list to hold dictionaries of all found interfaces with names, prtg object IDs etc
sensor_prestage_data = []
#calculate time it took to collect data
print "Data collection took", round(time.time() - start_time,2), "seconds to run\n"
#loop through all the entries in device list

for device in devices:
	#for each entry in device list, loop and enumerate through all results in interface list
	for index, entry in enumerate(interface_list):
		#if the first result in an entry of interface list matches the device entry in devices list, then print details and add to the prestage sensor list
		if  interface_list[index][0] in device['device']:
			#add info to new dictionary in prestage list, index + 1 because index starts at 0
			sensor_prestage_data.append({'OptionID' : index + 1, 'Device': interface_list[index][0] ,'PRTGObjID' : device['objid'],'Interface': interface_list[index][1], 'intSNMPid': interface_list[index][2],'ServiceName':interface_list[index][3]})
#sort the prestage list by option id
sensor_prestage_data = sorted(sensor_prestage_data, key=lambda k: k ['OptionID'])

print "Found", len(sensor_prestage_data), "matches for", service_to_search_for, "\n"
for entry in  sensor_prestage_data:
	print "Option:", entry['OptionID']
	print "Device:", entry['Device']
	print "PRTG ObjectID:", entry['PRTGObjID']
	print "Interface:", entry['Interface']
	print "Interface SNMP ID:", entry['intSNMPid']
	print "Service Name:", entry['ServiceName']
	print "--------------------------"
#user input section for choosing which sensors to add
counter = 1
#infinite loop so user can add multiple sensors
while 1:
	print "Select sensor to add.  Type the option number, or 'all' to add all sensors.  Ctrl+C to cancel"	
	selected_ID = raw_input("Sensor to add: ")
	#if user types all then add all sensors by setting a counter and looping through until the counter = the lenght of the list
	if selected_ID == "all":
		while counter < len(sensor_prestage_data) + 1:
			add_prtg_sensor(counter)
			counter = counter + 1
			
	else:
		selected_ID = int(selected_ID)
		add_prtg_sensor(selected_ID)
