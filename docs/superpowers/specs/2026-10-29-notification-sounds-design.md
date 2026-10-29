# Different sounds for different notifications (#91) — design

## Intent

Today every notification plays one sound chosen by `[notify] sound`: a random pick from the five
bundled sounds plus any file in `sounds_dir`, or one named sound, or nothing. The user wants
**more fun sounds**, and different ones depending on what the notification is about. They have
five sound packs (Orc Peon, Peasant, GLaDOS, Kerrigan, Battlecruiser) in the openpeon format and
want an agent that's waiting to say something like "Something need doing?", a memory alert to
sound like an alarm, and everything else to chime.

Success: with the packs copied in, a waiting agent plays a random voice line, a memory alert a
random "resource limit" line, and the rest a chime, with each of the three settable to one chosen
sound, to random, or to off.

## Goals

- Three **themes**, each with its own setting:
  - **waiting**: a session starts waiting (the hook's ping).
  - **memory**: the daemon's memory alerts (machine memory tight or critical, a project or pane
    over its RAM limit, RAM growing fast) and the OOM crash report. (My call on which kinds count
    as memory: everything about RAM.)
  - **other**: every other notification (CPU over its limit, the machine busy, anything added
    later).
- Each theme's setting is one of:
  - `"random"`: a random clip from the theme's pool, a new pick every time;
  - `"off"`: no sound; the desktop notification still shows (as today, `[notify] desktop`
    decides that);
  - a sound name: that sound every time.
- **Pools for random** (the user said: mix all packs, let them go at random):
  - waiting: every pack's `input.required` clips plus the `sounds_dir` files (the user put those
    there to hear them); the five bundled sounds join only when no pack has an `input.required`
    clip, which is today's pool exactly (my call: once packs are in, the beeps would dilute the
    fun ones);
  - memory: every pack's `resource.limit` clips; with none of those, an alarm-like bundled sound
    (`bell`; my call);
  - other: the bundled `chime` (random over a pool of one; my call: no pack category fits).
- **Packs are local only.** The user copies them into `src/cardak/sounds/packs/<pack>/`
  (`openpeon.json` plus `sounds/`), and that folder is git-ignored: the clips are CC-BY-NC and
  from commercial games, the repo is public, and nothing of them may land in a commit, the wheel
  or a release. cardak only reads them. The user's install is an editable one, so it sees the
  folder in place.
- **mp3** is accepted next to `.wav`, `.oga` and `.ogg` (two packs are mp3). Checked
  2026-10-29: `pw-play` links libsndfile 1.2.2, which decodes mp3; `paplay` (the fallback) goes
  through libsndfile too.
- Existing configs keep working: with no new keys and no packs, the waiting sound is what it is
  today.

## Non-goals

- Shipping, downloading or installing packs; a pack picker in the deck; previewing sounds.
- The packs' other categories (`session.start`, `task.complete`, `task.acknowledge`,
  `task.error`, `user.spam`): cardak has no notification for them today.
- Per-project sounds, volume control, rate limiting sounds beyond the existing repeat rules.
- Checking the packs' `sha256` fields.

## Anti-goals

- **Pack files in git.** A test or check that would fail if `src/cardak/sounds/packs/` held a
  tracked file is welcome; the wheel must not include it either.
- **A slower hook.** `cardak hook` is budgeted at < 150 ms and pings run in it. Reading a handful
  of small `openpeon.json` files only on the ping path is fine; nothing new on the non-ping path,
  and the hook's import guards (`tests/test_cli.py`) still hold.
- **A broken pack breaking notifications.** A missing, unreadable or malformed manifest, or a
  listed file that's missing, skips that pack or that clip. The deck may warn about it once, as
  it does for a missing `sounds_dir`; the hook and daemon never fail over it.
- Silence by accident: a pool that turns out empty falls back (waiting → today's pool, memory →
  `bell`, other → `chime`), never to no sound unless the setting is `"off"`.

## Settings

In `[notify]` (my call on names; the builder may adjust if a clearer one fits the file):

```toml
[notify]
desktop = true
waiting_sound = "random"   # "random" | "off" | a sound name
memory_sound = "random"
other_sound = "random"
sounds_dir = "~/sounds"    # unchanged: loose files, part of the waiting pool and nameable
```

- The old `sound` key stays valid and means `waiting_sound` when that isn't set (my call:
  `sound` affected every notification, but a user who named a sound most likely chose it for the
  waiting ping; memory and other keep their new defaults). If `sound = "off"` and no theme key is set, all three are off, so someone
  who silenced cardak stays silent.
- **Sound names**: a bundled sound by stem (`chime`), a `sounds_dir` file by file name (as
  today), and a pack clip as `<pack>/<file stem>` (`peon/PeonWhat3`), where `<pack>` is the
  pack's **folder name** under `packs/`, the name the user sees on disk, not the manifest's
  `name` (my call; folder names are unique by construction). Any clip a manifest lists can be
  named, whatever its category (my call: a `task.complete` line can be someone's waiting sound);
  only random is limited to the theme's category. An unknown name plays the
  theme's random pick and the deck warns, as today.

## Constraints

- Layering and the one-interface-per-format rule from AGENTS.md: openpeon's manifest is an
  external format. Its parsing goes in one place (a collector, e.g. `collectors/soundpacks.py`,
  importing nothing from cardak), and `core/alerts.py` picks from what it returns.
- Tests use small synthetic packs under `tmp_path` (a recorded manifest shape in
  `tests/fixtures/`, with neutral names and no real clips), never the user's packs.
- Docs: AGENTS.md (where packs go, that they're git-ignored, the themes, the config keys),
  CHANGELOG Unreleased → Added, one line (#91). The config reference wherever `[notify]` is
  documented today.

## Left to the builder

- How the packs folder is found (`files("cardak.sounds") / "packs"` keeps it next to the
  bundled sounds).
- The exact warning texts, and whether a pack with no usable clips warns.
- Whether `sounds_dir` clips also join the memory pool (my lean: no; they're untyped).
