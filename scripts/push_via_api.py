#!/usr/bin/env python3
"""Push local repo contents to GitHub via API — bypasses git transfer protocol."""

import base64
import json
import os
import sys
import urllib.request
import urllib.error

GITHUB_API = "https://api.github.com"
REPO = "athena254/The-Agency"
BRANCH = "main"

def get_token():
    # Try gh auth token first
    import subprocess
    try:
        result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
    except FileNotFoundError:
        pass
    return os.environ.get("GITHUB_TOKEN", "")

def api_request(method, path, data=None, token=None):
    url = f"{GITHUB_API}{path}"
    headers = {
        "Authorization": f"token {token}",
        "Content-Type": "application/json",
        "Accept": "application/vnd.github.v3+json",
    }
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"API error {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
        raise

def create_blob(content_bytes, token):
    """Create a git blob for raw file content."""
    b64 = base64.b64encode(content_bytes).decode()
    result = api_request("POST", f"/repos/{REPO}/git/blobs", {
        "content": b64,
        "encoding": "base64",
    }, token)
    return result["sha"]

def get_current_tree(token):
    """Get the current commit's tree SHA."""
    ref = api_request("GET", f"/repos/{REPO}/git/refs/heads/{BRANCH}", token=token)
    commit_sha = ref["object"]["sha"]
    commit = api_request("GET", f"/repos/{REPO}/git/commits/{commit_sha}", token=token)
    return commit["tree"]["sha"], commit_sha

def collect_files(root_dir, ignore_dirs=None):
    """Walk directory and collect files to push."""
    if ignore_dirs is None:
        ignore_dirs = {".git", ".venv", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                       "__pycache__", "node_modules", ".athena"}
    
    files = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        # Prune ignored directories
        dirnames[:] = [d for d in dirnames if d not in ignore_dirs and not d.startswith(".")]
        
        for filename in filenames:
            if filename.startswith(".") and filename not in {".gitignore", ".env.example", ".dockerignore"}:
                continue
            filepath = os.path.join(dirpath, filename)
            relpath = os.path.relpath(filepath, root_dir)
            files.append((relpath, filepath))
    return files

def main():
    token = get_token()
    if not token:
        print("ERROR: No GitHub token. Set GITHUB_TOKEN or run gh auth login.", file=sys.stderr)
        sys.exit(1)
    
    root_dir = os.getcwd()
    print(f"Collecting files from {root_dir}...")
    
    files = collect_files(root_dir)
    print(f"Found {len(files)} files to push")
    
    # Get current tree for parent commit
    current_tree_sha, parent_commit_sha = get_current_tree(token)
    print(f"Current tree: {current_tree_sha[:12]}...")
    
    # Create blobs for all files
    tree_entries = []
    for i, (relpath, filepath) in enumerate(files):
        with open(filepath, "rb") as f:
            content = f.read()
        
        # Skip large binaries and DB files
        if len(content) > 1024 * 1024:  # > 1MB
            print(f"  SKIP {relpath} ({len(content)} bytes)")
            continue
        
        sha = create_blob(content, token)
        tree_entries.append({
            "path": relpath.replace("\\", "/"),
            "mode": "100644",
            "type": "blob",
            "sha": sha,
        })
        
        if (i + 1) % 10 == 0:
            print(f"  Uploaded {i+1}/{len(files)} blobs...")
    
    print(f"Uploaded {len(tree_entries)} blobs")
    
    # Create tree
    print("Creating tree...")
    tree_result = api_request("POST", f"/repos/{REPO}/git/trees", {
        "base_tree": current_tree_sha,
        "tree": tree_entries,
    }, token)
    
    new_tree_sha = tree_result["sha"]
    print(f"New tree: {new_tree_sha[:12]}...")
    
    # Create commit
    print("Creating commit...")
    commit_result = api_request("POST", f"/repos/{REPO}/git/commits", {
        "message": "feat: push via API (ci pipeline, docker, security scans)",
        "tree": new_tree_sha,
        "parents": [parent_commit_sha],
    }, token)
    
    new_commit_sha = commit_result["sha"]
    print(f"New commit: {new_commit_sha[:12]}...")
    
    # Update branch ref
    print("Updating branch ref...")
    api_request("PATCH", f"/repos/{REPO}/git/refs/heads/{BRANCH}", {
        "sha": new_commit_sha,
    }, token)
    
    print(f"✅ Pushed to {BRANCH} branch successfully!")
    print(f"Commit: https://github.com/{REPO}/commit/{new_commit_sha}")

if __name__ == "__main__":
    main()
