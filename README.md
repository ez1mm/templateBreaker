# templateBreaker
## Meraki Template Tools
  These scripts allow you to unBind a MX template from a templated network WHILE it's in production with minimal impact to client traffic. 
  
  The script creates a new network with identical settings (addresses, ports, firewall, trafficshapping, autoVPN, etc) as the templated network and moves the hardware into the non-Templated network preserving the original. It'll also ensure the firmware matches on the destination network so your MX/Z3 device will have minimal client impact. In testing, the local outtage wasn't noticable and the autoVPN outtage was <20seconds. 
 
  **./unbind.py** \<networkID>  -   This unbinds a network where the networkID is a network that is currently BOUND to a template. (MX currently)
  
  **./rebind.py** \<networkID>  -   This reverses the unbind script, where the networkID is the networkID of the templated network (same used in unbinding) (MX)
  
  **./move.py** \<networkID> \<target_TemplateID>  -   This moves a network from one template, into another template. Preserving all settings. (MX/MS/MR/MV/MG)


