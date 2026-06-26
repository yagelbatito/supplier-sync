"""Explore the WizShop JSON API to understand shape of categories + items."""
import json, re, urllib.request, urllib.parse, ssl, sys

BASE = "https://shop4.wizsoft.com/vshop/WSHOP.wzx"
UC = "golyangifts"
ctx = ssl._create_unverified_context()


def fetch(params: dict) -> dict:
    qs = urllib.parse.urlencode(params)
    url = f"{BASE}?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, context=ctx, timeout=30).read().decode("utf-8", errors="replace")
    # WizShop returns JSON with each char as %uXXXX
    decoded = re.sub(r"%u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), raw)
    return json.loads(decoded)


# 1) Categories tree
print("=" * 60)
print("CATEGORIES TREE (HTTPREQ=14, VSTree=Yes)")
print("=" * 60)
tree = fetch({"Lang": "HE", "API": "Yes", "HTTPREQ": "14", "UC": UC, "VSTree": "Yes"})
print(f"Status: {tree.get('Status')}")
print(f"Top-level keys: {list(tree.keys())}")
print(f"OutTab len: {len(tree.get('OutTab', []))}")
print()
print("First entry of OutTab (preview):")
out = tree.get("OutTab", [])
if out:
    print(json.dumps(out[0], ensure_ascii=False, indent=2)[:2000])

# Walk the tree shallowly to extract categories
def walk_categories(node, depth=0, path=""):
    """The OutTab is a nested list. Each node: [name, name2, key, ?, ?, ?, ..., [[...children], parent_key]]"""
    if not isinstance(node, list):
        return
    # First few entries look like: [name, name?, key, ..., [children, parent_key]]
    if len(node) >= 3 and isinstance(node[0], str):
        name = node[0]
        key = node[2] if len(node) > 2 else ""
        new_path = f"{path}/{name}" if path else name
        print(f"  {'  ' * depth}- {name!r}  key={key!r}")
        # Find children — typically last element is [children_list, parent_key]
        for elem in node:
            if isinstance(elem, list) and elem and isinstance(elem[0], list):
                for child in elem[0]:
                    walk_categories(child, depth + 1, new_path)

print()
print("Walking tree (first few levels):")
for entry in out[:1]:  # top entry
    walk_categories(entry)
