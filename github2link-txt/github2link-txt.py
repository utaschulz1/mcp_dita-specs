import requests

# Get the tree from GitHub API (No key needed for one-off)
tree_url = "https://api.github.com/repos/oasis-tcs/dita/git/trees/master?recursive=1"
data = requests.get(tree_url).json()

# Mapping GitHub paths to Web URLs
base_web_url = "https://dita-lang.org/dita/"
links = []

for item in data.get('tree', []):
    path = item['path']
    # Filter for the actual spec content
    if path.startswith("specification/") and path.endswith(".dita"):
        # 1. Remove 'specification/' prefix
        # 2. Remove '.dita' suffix
        # 3. Handle 'langref' or 'archspec' logic
        url_path = path.replace("specification/", "").replace(".dita", "")
        # Skip container files — valid content pages have at least 3 segments (e.g. archSpec/base/topic)
        if url_path.count("/") >= 2:
            links.append(f"{base_web_url}{url_path}")

with open("links.txt", "w") as f:
    for link in sorted(set(links)):
        f.write(link + "\n")