# The container image

One image carries the headless half of Watchman: the `hn` CLI, the standing agents, the notification
bus, and the served web console. The desktop app is the deliberate exception: a GUI ships as native
platform bundles, never a container.

Images are built, vulnerability-scanned, and smoke-tested in CI, then published to GHCR:

```
ghcr.io/thwomp-io/watchman:<version> # one per release — pin this for anything durable
ghcr.io/thwomp-io/watchman:latest # moving pointer to the newest release
```

## Run engine commands

The image's entrypoint is the `hn` CLI — arguments are the lane + verb:

```bash
docker run --rm ghcr.io/thwomp-io/watchman --help
docker run --rm ghcr.io/thwomp-io/watchman finance networth --pack samples/packs/demo-investor
```

Your own corpus is **mounted, never baked in**. The image presets `TRACKER_PATH=/corpus`, so mounting
your vault there is the whole setup:

```bash
docker run --rm -v "$HOME/my-corpus:/corpus" ghcr.io/thwomp-io/watchman finance networth
```

Keyless verbs work with nothing else. Lanes that call keyed providers read their keys from the
environment (`--env-file .env` works as-is against the repo's `.env.example` names).

## Serve the web console

The image ships the built console UI at `/app/ui`:

```bash
docker run -d --name watchman -p 8787:8787 \
  -v "$HOME/my-corpus:/corpus" \
  -v watchman-home:/home/watchman \
  ghcr.io/thwomp-io/watchman bus serve --host 0.0.0.0 --console --ui /app/ui
```

- `--host 0.0.0.0` binds inside the container; publish the port as narrowly as your network warrants
  (`-p 127.0.0.1:8787:8787` keeps it host-local).
- The bearer token auto-generates into the home volume on first run. Read it with:

  ```bash
  docker exec watchman cat /home/watchman/.config/harness/bus-token
  ```

- Open `http://<host>:8787/` and paste the token at the prompt. Everything in
  [`WEB-CONSOLE.md`](WEB-CONSOLE.md) — phones, PWA install, variant mounts, satellites — applies
  unchanged.

The `watchman-home` volume holds state you want to survive restarts: the token, the bus database,
config. The corpus mount can be read-only (`-v …:/corpus:ro`) for a pure viewing node.

## Standing agents

The scheduled watchers (e.g. the finance pulse) are plain `hn` verbs — the same image runs them under
any scheduler you already have:

```bash
docker run --rm -v "$HOME/my-corpus:/corpus" -v watchman-home:/home/watchman \
  ghcr.io/thwomp-io/watchman finance pulse --notify
```

Point them at the same home volume and their events land on the same bus the console serves. A compose
file wiring console + agents as long-running services off this one image is on the roadmap; today each
piece is a one-liner.

## Build it yourself

```bash
docker build -t watchman .
```

The multi-stage build resolves locked Python deps, builds the console UI with the pinned npm lockfile,
and assembles a slim non-root runtime — no toolchain needed on the host beyond Docker.
