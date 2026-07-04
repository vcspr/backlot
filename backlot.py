#!/usr/bin/env python3
"""backlot — index every creative asset installed on your machine.

One file. No dependencies. Three commands.

    python3 backlot.py scan            # walk the machine, write backlot-index.json
    python3 backlot.py find "glitch"   # search everything you own
    python3 backlot.py stats           # what is actually on this machine

Scans (macOS paths, extendable via backlot.config.json):
  ae-preset     After Effects .ffx presets (app + user)
  resolve       DaVinci Resolve titles/transitions/generators/effects,
                including deep-scan INSIDE installed .drfx archives
  lut           .cube/.3dl/.dat LUTs (Resolve dirs + common locations)
  audio-plugin  VST / VST3 / AU / AAX bundles
  font          Installed font files

Output is flat JSON, built to be read by humans, scripts, and AI agents alike:
  {"type": "...", "name": "...", "category": "...", "path": "...", "source": "..."}
"""
import argparse
import json
import platform
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
INDEX_FILE = Path("backlot-index.json")
CONFIG_FILE = Path("backlot.config.json")

AUDIO_EXTS = {".vst": "VST", ".vst3": "VST3", ".component": "AU", ".aaxplugin": "AAX"}
LUT_EXTS = {".cube", ".3dl", ".dat", ".cql"}
FONT_EXTS = {".ttf", ".otf", ".ttc"}


def rel(p):
    """Home-relative display path; keeps usernames out of shared output."""
    try:
        return "~/" + str(Path(p).relative_to(HOME))
    except ValueError:
        return str(p)


def item(kind, name, category, path, source):
    return {"type": kind, "name": name, "category": category, "path": rel(path), "source": source}


# ---------------------------------------------------------------- scanners

def scan_ae():
    roots = []
    for app in Path("/Applications").glob("Adobe After Effects*"):
        roots.append((app / "Presets", "app"))
    for docs in (HOME / "Documents/Adobe").glob("After Effects*"):
        roots.append((docs / "User Presets", "user"))
    for root, source in roots:
        if not root.is_dir():
            continue
        for f in root.rglob("*.ffx"):
            cat = str(f.parent.relative_to(root)) if f.parent != root else ""
            yield item("ae-preset", f.stem, cat, f, source)


def _resolve_kind(inner):
    parts = [p.lower() for p in Path(inner).parts]
    for key, kind in (("titles", "resolve-title"), ("transitions", "resolve-transition"),
                      ("generators", "resolve-generator"), ("effects", "resolve-effect")):
        if key in parts:
            return kind
    return "resolve-effect"


def scan_resolve():
    bases = [
        (HOME / "Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Templates", "user"),
        (Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Templates"), "system"),
    ]
    for base, source in bases:
        if not base.is_dir():
            continue
        for f in base.rglob("*.setting"):
            yield item(_resolve_kind(f.relative_to(base)), f.stem,
                       str(f.parent.relative_to(base)), f, source)
        for drfx in base.rglob("*.drfx"):
            try:
                with zipfile.ZipFile(drfx) as z:
                    for inner in z.namelist():
                        if inner.endswith(".setting"):
                            name = Path(inner).stem
                            yield item(_resolve_kind(inner), name,
                                       str(Path(inner).parent), f"{drfx}!{inner}",
                                       f"pack:{drfx.stem}")
            except zipfile.BadZipFile:
                continue


def scan_luts():
    bases = [
        (HOME / "Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT", "user"),
        (Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT"), "system"),
    ]
    for base, source in bases:
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if f.suffix.lower() in LUT_EXTS and f.is_file():
                cat = str(f.parent.relative_to(base)) if f.parent != base else ""
                yield item("lut", f.stem, cat, f, source)


def scan_audio():
    bases = [
        (Path("/Library/Audio/Plug-Ins"), "system"),
        (HOME / "Library/Audio/Plug-Ins", "user"),
    ]
    for base, source in bases:
        if not base.is_dir():
            continue
        for folder in base.iterdir():
            if not folder.is_dir():
                continue
            for entry in folder.iterdir():
                fmt = AUDIO_EXTS.get(entry.suffix.lower())
                if fmt:
                    yield item("audio-plugin", entry.stem, fmt, entry, source)


def scan_fonts():
    bases = [(HOME / "Library/Fonts", "user"), (Path("/Library/Fonts"), "system")]
    for base, source in bases:
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if f.suffix.lower() in FONT_EXTS and f.is_file():
                yield item("font", f.stem, f.suffix.lstrip(".").upper(), f, source)


SCANNERS = {
    "ae-preset": scan_ae,
    "resolve": scan_resolve,
    "lut": scan_luts,
    "audio-plugin": scan_audio,
    "font": scan_fonts,
}


def scan_extra(config):
    """backlot.config.json: {"extra_dirs": {"<type>": ["/path", ...]}} — any file tree."""
    for kind, dirs in config.get("extra_dirs", {}).items():
        for d in dirs:
            base = Path(d).expanduser()
            if not base.is_dir():
                continue
            for f in base.rglob("*"):
                if f.is_file() and not f.name.startswith("."):
                    cat = str(f.parent.relative_to(base)) if f.parent != base else ""
                    yield item(kind, f.stem, cat, f, "extra")


# ---------------------------------------------------------------- commands

def cmd_scan(_args):
    config = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    items = []
    for kind, fn in SCANNERS.items():
        found = list(fn())
        items.extend(found)
        print(f"  {kind:<13} {len(found):>7,}")
    extra = list(scan_extra(config))
    if extra:
        items.extend(extra)
        print(f"  {'extra':<13} {len(extra):>7,}")
    index = {
        "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "machine": platform.node() and "local" or "local",
        "platform": platform.system(),
        "count": len(items),
        "items": items,
    }
    INDEX_FILE.write_text(json.dumps(index, indent=1))
    print(f"\n{len(items):,} assets -> {INDEX_FILE}")


def _load():
    if not INDEX_FILE.exists():
        sys.exit("No index yet. Run: python3 backlot.py scan")
    return json.loads(INDEX_FILE.read_text())


def cmd_find(args):
    q = args.query.lower()
    hits = []
    for it in _load()["items"]:
        name, cat = it["name"].lower(), it["category"].lower()
        score = (3 if q in name else 0) + (1 if q in cat else 0)
        if args.type and not it["type"].startswith(args.type):
            continue
        if score:
            hits.append((score, it))
    hits.sort(key=lambda h: (-h[0], h[1]["name"].lower()))
    hits = [h[1] for h in hits[: args.limit]]

    if args.json:
        print(json.dumps(hits, indent=1))
        return
    by_type = {}
    for it in hits:
        by_type.setdefault(it["type"], []).append(it)
    for kind, group in sorted(by_type.items()):
        print(f"\n── {kind} ({len(group)}) " + "─" * max(1, 40 - len(kind)))
        for it in group:
            cat = f"  [{it['category']}]" if it["category"] else ""
            src = f"  ({it['source']})" if it["source"].startswith("pack:") else ""
            print(f"  {it['name']}{cat}{src}")
    if not hits:
        print(f'nothing matching "{args.query}" — try a shorter query')
    else:
        print(f"\n{len(hits)} shown")


def cmd_stats(args):
    idx = _load()
    counts = {}
    for it in idx["items"]:
        counts[it["type"]] = counts.get(it["type"], 0) + 1
    if args.json:
        print(json.dumps({"scanned_at": idx["scanned_at"], "total": idx["count"], "by_type": counts}, indent=1))
        return
    print(f"\nindexed {idx['count']:,} assets · {idx['scanned_at']}\n")
    for kind, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        bar = "█" * max(1, int(n / max(counts.values()) * 34))
        print(f"  {kind:<19} {n:>7,}  {bar}")
    print()




def cmd_serve(_args):
    """MCP server mode: JSON-RPC over stdio, so AI agents query the index natively.
    Register with any MCP client as: python3 backlot.py serve"""
    idx = _load()

    def find(query, type_filter=None, limit=25):
        q = query.lower()
        hits = []
        for it in idx["items"]:
            score = (3 if q in it["name"].lower() else 0) + (1 if q in it["category"].lower() else 0)
            if type_filter and not it["type"].startswith(type_filter):
                continue
            if score:
                hits.append((score, it))
        hits.sort(key=lambda h: (-h[0], h[1]["name"].lower()))
        return [h[1] for h in hits[:limit]]

    def stats():
        counts = {}
        for it in idx["items"]:
            counts[it["type"]] = counts.get(it["type"], 0) + 1
        return {"scanned_at": idx["scanned_at"], "total": idx["count"], "by_type": counts}

    TOOLS = [
        {"name": "find_assets",
         "description": "Search every creative asset installed on this machine (AE presets, Resolve templates, LUTs, audio plugins, fonts).",
         "inputSchema": {"type": "object", "properties": {
             "query": {"type": "string", "description": "search text, e.g. 'glitch'"},
             "type": {"type": "string", "description": "optional filter: ae-preset, resolve, lut, audio-plugin, font"},
             "limit": {"type": "integer", "default": 25}}, "required": ["query"]}},
        {"name": "asset_stats",
         "description": "Counts of installed creative assets by type.",
         "inputSchema": {"type": "object", "properties": {}}},
    ]

    def reply(msg_id, result=None, error=None):
        out = {"jsonrpc": "2.0", "id": msg_id}
        if error:
            out["error"] = error
        else:
            out["result"] = result
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, msg_id = msg.get("method"), msg.get("id")
        if method == "initialize":
            reply(msg_id, {"protocolVersion": "2024-11-05",
                           "capabilities": {"tools": {}},
                           "serverInfo": {"name": "backlot", "version": "0.2.0"}})
        elif method == "tools/list":
            reply(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            name = msg["params"]["name"]
            args = msg["params"].get("arguments", {})
            if name == "find_assets":
                data = find(args.get("query", ""), args.get("type"), args.get("limit", 25))
            elif name == "asset_stats":
                data = stats()
            else:
                reply(msg_id, error={"code": -32601, "message": f"unknown tool {name}"})
                continue
            reply(msg_id, {"content": [{"type": "text", "text": json.dumps(data, indent=1)}]})
        elif msg_id is not None:
            reply(msg_id, error={"code": -32601, "message": f"unknown method {method}"})


def main():
    ap = argparse.ArgumentParser(description="Index every creative asset installed on your machine.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan", help="walk the machine, write backlot-index.json")
    f = sub.add_parser("find", help="search the index")
    f.add_argument("query")
    f.add_argument("--type", help="filter: ae-preset, resolve, lut, audio-plugin, font")
    f.add_argument("--limit", type=int, default=40)
    f.add_argument("--json", action="store_true", help="machine/agent-readable output")
    s = sub.add_parser("stats", help="counts per asset type")
    s.add_argument("--json", action="store_true")
    sub.add_parser("serve", help="MCP server mode (JSON-RPC over stdio) for AI agents")
    args = ap.parse_args()
    {"scan": cmd_scan, "find": cmd_find, "stats": cmd_stats, "serve": cmd_serve}[args.cmd](args)


if __name__ == "__main__":
    main()
