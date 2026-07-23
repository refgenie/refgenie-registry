import os, json
from refget.store import RefgetStore
base = os.environ["REFGETSTORE_BASE"]
s = RefgetStore.on_disk(os.path.join(base, "plantref"))
print("plantref stats:", json.dumps(dict(s.stats())))
