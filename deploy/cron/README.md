# Scheduled evidence maintenance

`glassbox.adapters.inbound.cli.maintenance` (invoked as
`python -m glassbox.adapters.inbound.cli.maintenance`) is library code with no
built-in scheduler: something outside the process must call it periodically,
or evidence-table partitions stop being topped up and sealed segments stop
being purged. This directory ships two reference schedules -- pick the one
that matches your deployment target and adapt it. Neither file is wired into
CI or any Python entry point; they are deployment artifacts, not application
code.

Both examples run the entrypoint every 15 minutes. A slower cadence is safe
(`GLASSBOX_MAINTENANCE_SEGMENT_BATCH_LIMIT` bounds one run's work either way);
a faster cadence is unnecessary since `seal_after_seconds`/`purge_grace_seconds`
default to whole days.

## Kubernetes: `k8s-cronjob.yaml`

Fill in the placeholders (`<...>`) and `kubectl apply -f k8s-cronjob.yaml`.
Secrets referenced by `GLASSBOX_EVIDENCE_DSN` / `GLASSBOX_SIGNING_*` should
come from a `Secret`/external-secrets integration, never a literal in the
manifest.

## systemd: `glassbox-maintenance.service` + `glassbox-maintenance.timer`

For a bare VM/host deployment. Copy both units to `/etc/systemd/system/`,
then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now glassbox-maintenance.timer
```

Check the last run and next scheduled run with:

```bash
systemctl status glassbox-maintenance.timer
journalctl -u glassbox-maintenance.service --since -1d
```

## How staleness is surfaced if the schedule stops running

The entrypoint has no separate "last run" heartbeat table (adding one would
need its own schema migration). Instead, every run inspects the oldest
segment that still has outstanding retention work: if maintenance is running
on schedule, no segment should ever sit with unsealed/unpurged work past
`seal_after_seconds + purge_grace_seconds`. A segment older than that
(`+ GLASSBOX_MAINTENANCE_STALENESS_THRESHOLD_SECONDS` grace, default an extra
7 days) is itself evidence the scheduled job has stopped running, and the run
logs a `WARNING`-level `"evidence maintenance appears stale"` message with
the measured age -- alert on that log line (or on the *absence* of the
routine `"evidence maintenance run complete"` `INFO` log for longer than the
schedule's expected cadence), not on a bespoke metric.
