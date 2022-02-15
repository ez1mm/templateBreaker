# templateBreaker
## Meraki Template Tools
  These scripts allow you to unBind a MX template from a templated network WHILE it's in production with minimal impact to client traffic. The script creates a new network with identical settings (addresses, ports, firewall, trafficshapping, autoVPN, etc) as the templated network and moves the hardware into the non-Templated network. It'll also ensure the firmware matches on the destination network so your MX/Z3 device will have minimal client impact. In testing, the local outtage wasn't noticable and the autoVPN outtage was <20seconds. There is no reboot of the hardware.
  
 
  ./unbind.py <networkID>  -   This unbinds a network where the networkID is a network that is currently BOUND to a template
  ./rebind.py <networkID>  -   This reverses the unbind script, where the networkID is the networkID of the templated network (same used in unbinding)
