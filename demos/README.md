# Terminal recordings

The recordings the documentation site plays. Every one of them is a real run: the process
is spawned, its output is captured as it arrives, and the arrival times become the
playback timing. Nothing here is a transcript.

```bash
poe demos               # re-record every scene
poe demos failure       # or just one
```

## How a scene is put together

`scenes.toml` declares each one:

| Key | What it does |
|---|---|
| `name` | The output file, `demos/<name>.termshow`, and the `file=` a page passes |
| `title` | The label above the player |
| `cols` | Terminal width. The recorder exports it as `COLUMNS`, so the run really is that wide |
| `commands` | The commands to run, in order, in one session |
| `files` | Written into a throwaway project before the commands run |
| `dir` | The directory name that project gets. It shows up in tracebacks, so it is worth choosing |
| `cwd` | `"."` to run in this repository instead of a throwaway project |

The scene files live in `scenes.toml` rather than in the tree because one of them fails on
purpose, and a `test_*.py` that is meant to fail is a trap for anyone who points a runner
at the repository root.

## What is real and what is drawn

The program output is real, down to the order the two streams interleave. rustest writes
per-test lines to stdout and the summary to stderr, and the recorder merges them the way a
terminal does rather than concatenating one after the other.

Two things are drawn, because there is no shell in the loop to produce them: the `$`
prompt, and the keystrokes of the command being typed. Everything after the newline came
from the process.

## Playing one on a page

```markdown
{{< termshow file="quickstart" autoplay="false" loop="false" >}}
```

`autoplay="false"` does not mean the recording sits there waiting to be clicked. A small
IntersectionObserver in `great-docs.yml` starts each player as it scrolls into view, which
is what the shortcode's own `autoplay` cannot do: that one fires at page init, so a
five-second recording below the fold has finished before anyone reaches it.

great-docs pre-renders each `.termshow` into SVG keyframes at build time and inlines them,
so playback needs no network and the frames stay sharp at any zoom. The player, the
shortcode and the SVG renderer are all great-docs'; this directory only supplies the
recordings.

## Re-recording

Timings change on every run, so a re-record always produces a diff even when the output is
identical. Re-record when the output itself changes, not on a schedule.
