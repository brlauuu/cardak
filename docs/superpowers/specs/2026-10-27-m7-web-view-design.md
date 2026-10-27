# M7: the deck on a phone — design

PLAN.md §11 M7. This replaces the plan's "`cardak web` via textual-serve, bound to 127.0.0.1":
the user wants to read the deck from a phone or iPad on the same Wi-Fi, which a terminal
streamed into a browser does badly and a loopback address can't reach at all.

## Why

Away from the desk (couch, kitchen, another room) the user wants to glance at how their work is
going: which agents are working, which are waiting on them and why, and whether anything is
eating memory. Today that means walking back to the computer. The answer to "should I go back
now?" is already in cardak's snapshot; M7 puts it on the phone.

## Goals

1. **A read-only web page** made for touch screens, served on the home network, showing:
   - the machine's RAM and CPU, with the memory warning level;
   - **Waiting on you** first, oldest first: tag (approve / decide / review), project, how long
     it has waited, and the one-line summary when there is one;
   - every project with its accent color, RAM, a short RAM trend and a status dot per agent, and
     Other;
   - tap a project: its panes (program, folder, RAM, CPU, agent status);
   - tap a Claude session: what it needs, the user's last prompt and what the turn changed —
     the content of the deck's `i` panel.
2. **Always there when cardak's background service runs** (`cardak daemon`, the systemd user
   service from M5), whether or not the deck is open. No command to remember.
3. **Locked with a secret key.** Without the key a request gets no data.
4. **Updates itself** every few seconds, and says so plainly when the computer can't be reached
   or the data is old, rather than showing stale numbers as live.
5. **One-time setup**: `cardak web setup` turns it on, creates the key, prints the address and a
   QR code to scan.

## Non-goals

- **Any action.** No checkpoint, no jump, no theme, no answering prompts, no "mark seen". The
  user said "only look".
- Full transcripts, history (`h`), the event log (`e`), recap (`r`). The `i` content is enough
  for a glance. (Later, if asked.)
- Reaching it from outside the home network: no relay, no port forwarding, no accounts.
- HTTPS. Plain HTTP on the LAN; see Security.
- The textual-serve version of the terminal deck.

## Anti-goals (a failure even if it works)

- **Changing anything.** Opening the page on a phone must not change what the deck shows on the
  computer. In particular it must never mark a waiting session seen: looking at the phone is
  not looking at the pane, and the deck's "seen" decides notifications (M4).
- **Leaking to the network.** Anyone else on the Wi-Fi who opens the address, guesses paths, or
  hosts a page that makes the user's browser call it, gets nothing without the key.
- **Costing the daemon.** The daemon is budgeted at under 1% of a core and ~28 MB (M5). An open
  tab must not add another poll of WezTerm or `/proc`; the page reads the snapshot the daemon
  already took.
- **Lying about freshness.** A phone left on the page overnight must show that the numbers are
  from last night, not present them as now.

## How it fits

- **Served by the daemon.** When `[web] enabled = true`, `cardak daemon` starts a small HTTP
  server on a background thread. Every poll (every 2 s) the daemon hands its latest
  `DeckSnapshot` to the server; requests read that copy. Nothing in a request triggers a poll.
- **The daemon gains agent status only when the web view is on.** Today it polls through
  `LiveSource` with no store, so its snapshots have no agents and no Waiting group. With the web
  on it passes the store and the Anthropic key path, exactly as the deck does, and the focus
  history (`~/.local/state/cardak/focus`), but in a **read-only mode**: a wait the focus history
  says was seen is shown as the deck would show it after `mark_seen` (the same transition,
  applied to the in-memory copy), and **nothing is written**. So a wait the user looked at in
  WezTerm while the deck was closed leaves "Waiting on you" on the phone too, and the deck still
  makes the real `mark_seen` when it next runs. (Builder check; my call: the alternative, a
  phone listing waits the user already saw for hours, defeats the glance.) A test proves no
  write reaches the store from the daemon's poll or from any request. With the web off the
  daemon behaves exactly as today.
- The alerts, history rollup and crash watch already skip the Waiting group (they iterate
  `PROJECT_KINDS` / `"other"` only); a test pins that the daemon's alerts and minute rows are
  the same with agents present as without.
- **Layering.** A new front-end package `cardak/web/`, beside `ui/`: `cli → web → core`. `core`
  never imports it. `core/daemon.py` gets a generic "on snapshot" callback (`Callable[[DeckSnapshot,
  float], None]`); `cli.py` wires the web server into it. AGENTS.md's layout and layering
  sections gain `web/`.
- **No new runtime dependency for serving.** Python's `http.server`
  (`ThreadingHTTPServer`) is enough for one user and a few devices. The page is one static
  HTML file with inline CSS and JS shipped in the wheel (`src/cardak/web/page.html`), no build
  step, no external fetches (no CDN, no fonts).
- **QR code**: `segno` (pure Python, no dependencies) imported only by `cardak web setup`.
  Builder's call if a better-measured option exists; it must not be imported by the daemon or
  the hook.

## The server

- `GET /` → the page. It contains no data and needs no key.
- `GET /api/snapshot` → the JSON view of the latest snapshot (below). Needs the key.
- `GET /api/session/<session_id>` → the detail view of one **live** session (below). Needs the
  key. An unknown or ended session is 404.
- Anything else: 404. Any method other than `GET` (and `HEAD`): 405. There is no handler that
  writes.
- **The key** travels in an `Authorization: Bearer <key>` header, checked with
  `hmac.compare_digest`. A missing or wrong key is 401 with an empty body.
- Headers on every response: `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, and on the page a `Content-Security-Policy` that allows only
  its own inline script/style and `connect-src 'self'`. No CORS headers.
- Errors in a handler are logged to the daemon's stderr (the journal) and answered 500; they
  never stop the daemon's poll loop. The server thread is a daemon thread; the service stops it
  on SIGTERM with the rest.
- Request logging off (the default `http.server` log line would print every 3 s per tab).
- If the port is taken or the address can't be bound, the daemon logs one line and runs
  without the web view (alerts must keep working).

## The JSON view

Built in `cardak/web/view.py` from `DeckSnapshot` by an explicit whitelist (never
`dataclasses.asdict`), so new snapshot fields don't leak by default. Pids and `is_self` are
never sent.

- top level: `ts` (when the daemon took it, unix seconds), `now` (server time at the response),
  `error` (WezTerm unreachable / internal, as the deck words it), `warnings` (config error and
  warnings, store / summary warnings), `machine` (`ram_used`, `ram_total`, `cpu_pct`, `swap…`
  as the deck's header shows, `level`), `waiting` (list), `projects` (list, including Other last).
- a project: `name`, `kind`, `path` (tilde form), `accent` (hex or null), `rss`, `cpu_pct`,
  `ram_spark` (the deck's braille string is fine; the page may draw its own from a list of
  recent values instead — builder's call), `over`, `panes`.
- a pane: `pane_id`, `label`, `cwd_display`, `rss`, `cpu_pct`, `over`, `mcp_rss`, and `agent`
  (`session_id`, `status`, `kind`, `since`, `summary`) or null. Docker lines as `{label, rss,
  cpu_pct}`.
- waiting entries: `session_id`, `project`, `kind`, `since`, `summary`, `reason` (cut as the
  deck cuts it).
- `/api/session/<id>`: `project`, `status`, `kind`, `since`, `summary`, `reason`, `last_prompt`
  and `last_prompt_at`, the turn counts (edits, reads, commands, other) and `turn_files` (shown
  relative to the session's cwd, as the deck does). Same text cutting as `detail_text`; the
  shared cutting helpers move to `core` if the builder needs them outside `ui`. No transcript,
  no spend line.

## The page

- Mobile first, dark, calm, dense enough to see everything on one phone screen for a typical
  3–5 projects. On a wide screen (iPad landscape) two columns: Waiting + machine on the left,
  projects on the right. Mockup B from the brainstorm (`.superpowers/brainstorm/`, not
  committed) is the reference; colors follow the deck's (waiting orange, working green, idle
  grey, project accents).
- Polls `/api/snapshot` every 3 s with `fetch`, only while the tab is visible
  (`visibilitychange`); polls once immediately on becoming visible again.
- **Freshness**, shown in the header:
  - normal: "updated 2 s ago" (from `now − ts`);
  - snapshot older than 10 s: the header turns warning-colored, "last update 3 min ago";
  - fetch failing (computer asleep, daemon stopped, Wi-Fi changed): "can't reach cardak · last
    data from 21:14", the old data greyed out, still readable;
  - no key on this device: "open the link from `cardak web setup` (or scan its QR code) on
    this device";
  - 401: "this link's key is no longer valid · run `cardak web setup` on the computer".
- Tap a project to expand its panes (several can be open; kept across refreshes). Tap a
  session (in Waiting or in a project) to open its detail as a sheet over the list; it refreshes
  with the poll and says "this session has ended" when it 404s.
- **The key** lives in the URL fragment: `http://<ip>:<port>/#k=<key>`. The fragment never
  reaches the server or a log. The page **leaves it in the address bar**, so a Safari bookmark
  and an iOS/iPadOS "Add to Home Screen" icon both keep it (a home-screen app has its own
  storage, so a key kept only in `localStorage` would not reach it). It also copies the key into
  `localStorage`, so a later load of the bare address still works in the same browser. (Builder
  check; my call: the cost is that the key shows in the address bar when the phone's screen is
  shared; `--new-key` is the remedy.) `cardak web setup` says to open the link or scan the QR
  code on each device, and to bookmark or add to the home screen from that page. Text from the
  server is inserted with `textContent`, never `innerHTML` (prompts and summaries are
  arbitrary text).
- All times shown as ages ("12m"), computed from the server's `now`, so a phone with a wrong
  clock still shows the right ages.

## Setup and config

- `projects.toml`:
  ```toml
  [web]
  enabled = false    # cardak web setup sets it to true
  port = 8765
  host = "0.0.0.0"   # every interface; set a LAN address to listen on one
  ```
  Unknown keys and bad values are config warnings, as other sections do.
- `~/.config/cardak/web_key`: the key, `secrets.token_urlsafe(32)`, mode 0600, written
  atomically. The daemon refuses to serve (logs why) when the file has group or other bits, like
  the Anthropic key. It is read at daemon start.
- **`cardak web setup [--new-key] [--yes]`**:
  1. Creates the key if missing (`--new-key` replaces it: every device with the old link stops
     working, which the command says).
  2. Sets `[web] enabled = true` with tomlkit (comments kept), after asking unless `--yes`.
  3. Finds the LAN address (the source address of a UDP socket "connected" to a private
     address; no packet is sent), prints `http://<ip>:<port>/#k=<key>` and its QR code in the
     terminal.
  4. If cardak's service is installed and running, offers to restart it (`systemctl --user
     restart cardak`); otherwise says to run `cardak resources install`.
  5. Prints one line about firewalls: if a firewall such as ufw is on, allow the port from the
     local network (with the example command). cardak never changes firewall rules.
- **`cardak web off`**: sets `enabled = false` and offers the restart. The key file stays.
- **`cardak web status`**: enabled or not, the address, whether the daemon is serving (a `GET /`
  to itself on 127.0.0.1 when the host covers it), and how to show the QR code again
  (`cardak web setup` again is harmless: it keeps the key and re-prints).
- The daemon reads `[web]` at start only; a change needs a restart, which the commands offer.

## Security

- The threat is other people and devices on the same Wi-Fi (guests, a smart TV, a compromised
  laptop) and web pages the user visits that try to reach LAN addresses from their browser.
- The 256-bit key, required on every data request, stops both: a guessing client gets 401s,
  and a malicious page in the user's browser can't read responses (no CORS) or send the
  header without knowing the key. DNS rebinding gains nothing without the key.
- Over plain HTTP the key and the data cross the Wi-Fi unencrypted: someone already capturing
  traffic on that network could read them. Accepted for a home network (proposed in the
  brainstorm as a call; the user agreed); `cardak web setup` says it once in its output.
  `--new-key` is the remedy if a link leaks.
- The server never writes: only `GET`, read-only queries on the store, no `mark_seen`.

## Testing

- `web/view.py` against snapshots built in tests (the existing builders), with a whitelist test:
  pids and `is_self` never appear in the JSON.
- The server on an ephemeral port in tests: 401 without/with a wrong key, 200 with it, 405 for
  POST, 404 for unknown paths and ended sessions, the headers, and no request logging.
- The daemon: with `[web]` on, the poll passes a store and the focus history read-only; a
  wait the focus history saw is shown as seen, and a store fake that raises on `mark_seen` (or
  any write) stays untouched across polls and requests. With it off, the daemon is
  built as today. A bind failure leaves the poll loop running.
- `cardak web setup|off|status` like the other setup commands (scratch `XDG_*`, `--yes`).
- The page: its JS is small enough to check by hand; the live check opens it in a headless
  browser against a daemon on a scratch port with scratch `XDG_*` dirs and a headless
  `wezterm-mux-server`, as in M6, and checks the waiting list, a project's panes, the detail
  sheet, the stale and unreachable states, and the 401 state. The final check on a real phone
  is the user's.
- The `cardak hook` import guard tests stay green: nothing in the hook path imports `web`.

## Left to the builder

- The exact page layout and styling within the above; the sparkline drawing.
- How the daemon passes the snapshot to the server (a lock-protected holder is enough).
- What counts as a live session for `/api/session`: present in the latest snapshot is fine.
- The tag words: permission → approve, question → decide, finished → review, as the deck.
- The CSP mechanism for the inline script and style (hash or nonce).
- Whether `ram_spark` goes out as the braille string or as numbers.
- The QR library, within "pure Python, imported only by `cardak web setup`".
- Release: a minor bump (0.10.0).
