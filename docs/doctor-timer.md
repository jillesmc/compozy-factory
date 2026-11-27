# Alert on stopped factory agents

`bin/doctor --fix` wakes main sessions a daemon shutdown left stopped (nothing else wakes them: their health
is `dead`) and exits 1 when an instance is still `missing`, `paused` or needs `attention`. A systemd user
timer runs it every 15 minutes and raises a desktop notification with its output; `warn` and `fixed` lines
alone stay silent.
Replace the checkout path if yours differs.

```ini
# ~/.config/systemd/user/factory-doctor.service
[Unit]
Description=compozy-factory doctor

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'out=$(%h/data/projects/platform/compozy-factory/bin/doctor --fix 2>&1) || notify-send -u critical "factory doctor" "$out"'
```

```ini
# ~/.config/systemd/user/factory-doctor.timer
[Unit]
Description=Run compozy-factory doctor every 15 minutes

[Timer]
OnCalendar=*:0/15
Persistent=true

[Install]
WantedBy=timers.target
```

```console
$ systemctl --user daemon-reload
$ systemctl --user enable --now factory-doctor.timer
$ journalctl --user -u factory-doctor.service -n 20   # last runs
```

`--fix` never resumes a paused instance: an `unattended` or `fatigue` pause is the daemon's safety brake, so
read the main session first, then `compozy agent instance resume <id>`. A stopped main with a pending
approval, clarification or escalation is reported, not woken.
