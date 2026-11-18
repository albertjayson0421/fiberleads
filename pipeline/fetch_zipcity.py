import json, urllib.request, time, collections
import flpaths
target=sorted(set(open(flpaths.data("zips.txt")).read().split()))
nad=json.load(open(flpaths.data("zip_city.json")))
out={}; miss=[]
for i,z in enumerate(target):
    try:
        d=json.load(urllib.request.urlopen("https://api.zippopotam.us/us/%s"%z, timeout=12))
        places=d.get("places",[])
        if places:
            out[z]=places[0]["place name"].upper()
    except Exception:
        miss.append(z)
    if (i+1)%50==0: print("  fetched %d/%d"%(i+1,len(target)), flush=True)
# merge: prefer NAD address-specific city; fallback to USPS primary city
final={}
for z in target:
    final[z]= nad.get(z) or out.get(z) or ""
json.dump(final, open(flpaths.data("zip_city_final.json"),"w"))
have=sum(1 for z in target if final[z])
print("USPS fetched:", len(out), "| merged coverage:", have, "of", len(target))
print("STILL missing city:", [z for z in target if not final[z]])
