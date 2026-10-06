"""Lightweight release checker for TUK."""
from __future__ import annotations
import json, urllib.request
from packaging.version import Version

RELEASE_API="https://api.github.com/repos/FatihMakes/Mark-LV/releases/latest"

def check_latest(current: str):
    try:
        req=urllib.request.Request(RELEASE_API,headers={"Accept":"application/vnd.github+json","User-Agent":"TUK"})
        with urllib.request.urlopen(req,timeout=4) as r: data=json.load(r)
        tag=str(data.get("tag_name","")).lstrip("v")
        return {"available": bool(tag and Version(tag)>Version(current)), "version":tag, "url":data.get("html_url","")}
    except Exception:
        return {"available":False,"version":current,"url":""}
