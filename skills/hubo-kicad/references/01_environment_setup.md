# 01 Environment setup (Windows)

> Run `scripts/doctor.py` first (with KiCad's bundled python); it lists item by item what is missing and how to fix it. The table below is the manual procedure.

| Step | Action | Verify |
|---|---|---|
| 1 | Install KiCad 10 (minimum 9.0; `import_pcb` needs 10). To avoid the C drive: `winget install --id KiCad.KiCad -e --location "<install dir>"` | `"<KiCad>/bin/python.exe" -c "import pcbnew; print(pcbnew.GetBuildVersion())"` |
| 2 | **Open KiCad manually once after the first install** so it initializes the global symbol/footprint library tables | Otherwise ERC reports many "footprint library not included in the current configuration" errors, and `search_symbols` returns empty without explanation |
| 3 | Node.js 18+ | `node --version` |
| 4 | Get the KiCAD-MCP-Server source and run `npm install` in the repository directory (it runs `npm run build` automatically) | `dist/index.js` is generated |
| 5 | Install the Python dependencies **into KiCad's bundled python**: `"<KiCad>/bin/python.exe" -m pip install -r requirements.txt shapely` | Output shows `Successfully installed ...` |
| 6 (optional) | Auto-routing: Freerouting jar + portable JRE. **The JRE version follows the jar's compile target** (2.0.x needs 21, 2.2.2 and later need 25); verify the sha256 after download; set the environment variables `FREEROUTING_JAR` and `FREEROUTING_JAVA` | `doctor.py` shows both the jar's compile target and the Java version as OK |

> ⚠️ On Windows, `pcbnew` exists only in KiCad's bundled python; importing it with the system python fails. All scripts in this skill run with it.
>
> ⚠️ Non-configurable locations on the C drive: the MCP's JLCPCB parts database (`download_jlcpcb_database`) is fixed at `%USERPROFILE%\.kicad-mcp\data` (about 1.5 GB). If you do not want it on the C drive, skip the download and verify part numbers one by one on lcsc.com. The first Freerouting run creates `%APPDATA%\freerouting\freerouting.json` (a few KB).

## Register the MCP in Claude Code

When KiCad is installed in a non-default location, the server **cannot auto-detect it** (it only looks in `%LOCALAPPDATA%\Programs\KiCad`, `C:\Program Files\KiCad` and `C:\Program Files (x86)\KiCad`), so `KICAD_PYTHON` and `KICAD_CLI` must be given explicitly:

```bash
MSYS_NO_PATHCONV=1 claude mcp add kicad --scope user \
  -e "KICAD_PYTHON=<KiCad>\bin\python.exe" \
  -e "KICAD_CLI=<KiCad>\bin\kicad-cli.exe" \
  -e LOG_LEVEL=info \
  -- node "<KiCAD-MCP-Server>\dist\index.js"
```

- `MSYS_NO_PATHCONV=1` stops Git Bash from rewriting paths; `--scope user` writes to `~/.claude.json`; use `--scope project` if you want it for one project only.
- Verify: `claude mcp get kicad` → `Status: ✔ Connected`; `/mcp` in a session shows `kicad`.
- After changing the MCP source, run `npm run build` and restart Claude Code.
- On the Codex side, register the same command and environment variables the way Codex configures MCP (not tested on Codex).

## Running Freerouting

- First turn off statistics in `%APPDATA%\freerouting\freerouting.json` (`disable_analytics=true`, `allow_telemetry=false`).
- When running, clear `HTTP_PROXY` / `HTTPS_PROXY` and add `--gui.enabled=false`:
  `"$FREEROUTING_JAVA" -jar "$FREEROUTING_JAR" -de <in.dsn> -do <out.ses> -mp <passes> --gui.enabled=false`
