
import json


def is_axion_target(target: dict) -> bool:
    return "axion" in target.get("method", {}).get("declaringClass", "")


with open("boundaries.json") as f:
    data = json.load(f)

filtered_methods = []

print("len of boundary methods: ", len(data.get("boundary_methods", [])))

for method in data.get("boundary_methods", []):
    new_callsites = []

    if "axion" in method.get("caller", "").get("declaringClass", ""):
        filtered_methods.append(method)
        continue
        
    # for cs in method.get("callSites", []):
    #     new_targets = [t for t in cs.get("targets", []) if is_axion_target(t)]

    #     if new_targets:
    #         # shallow copy to avoid mutating input JSON
    #         cs_copy = dict(cs)
    #         cs_copy["targets"] = new_targets
    #         new_callsites.append(cs_copy)

    # if new_callsites:
    #     method_copy = dict(method)
    #     method_copy["callSites"] = new_callsites
    #     filtered_methods.append(method_copy)

print("len of filtered boundary methods: ", len(filtered_methods))

# with open("new_boundaries.json", "w") as f:
#     json.dump(
#         {"boundary_methods": filtered_methods},
#         f,
#         indent=2,
#         sort_keys=True
#     )
