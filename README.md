# EAC Cyprus — Home Assistant integration

Home Assistant integration for the **EAC** (Electricity Authority of Cyprus) Distribution Web Portal at `meterreading-dso.eac.com.cy`.

Pulls electricity-meter readings into HA so you can use them in the Energy Dashboard, in automations, or just to keep an eye on consumption. Built on top of the [`eac-dso-portal`](https://github.com/santafox/eac-dso-portal) Python library (also published on [PyPI](https://pypi.org/project/eac-dso-portal/)).

## What you get

- One Home Assistant **device per active service point** at your address, identified with the meter's manufacturer/model/serial.
- One **sensor per measurement channel** the meter actually reports — typically:
  - `Energy total (24h)` — cumulative kWh, ready for the Energy Dashboard
  - `Energy peak tariff` / `Energy off-peak tariff` — for two-tariff plans
  - `Energy import (30-min profile)` — last 30-minute slot, in kWh
  - `Energy exported` / `Energy export (30-min profile)` — populated automatically once you start exporting (PV)
  - reactive / apparent energy channels — disabled by default

Sensors appear *only* for channels that the API actually returns data for, so the entity list stays tidy. New channels start appearing automatically when the meter starts reporting them (e.g. after a tariff change or a PV install).

## Install

### Via HACS (recommended once published)

Coming soon — HACS submission pending.

### Manual

1. Copy `custom_components/eac_cyprus/` into your HA config's `custom_components/` folder (e.g. via the Samba add-on at `\\<HA-IP>\config\custom_components\`).
2. Restart Home Assistant.
3. Settings → Devices & Services → **+ Add Integration** → search for **EAC Distribution Web Portal**.
4. Enter the email and password you use to sign in at `meterreading-dso.eac.com.cy`.

The first poll happens immediately; afterwards data refreshes every 6 hours (the portal itself updates daily, more often is wasteful).

## Design notes — devices, service points, and meters

### How EAC structures the data

- A **service point** (`spId`, 12-digit) is a permanent supply contract tied to an address. It does not change.
- A **meter** is the physical hardware at that point. It can be replaced (e.g. mechanical → smart), and the API exposes the full history (`installDate`, `removalDate`, serial number, model).
- A meter can have multiple **measurement channels** (`mcList`) over its lifetime — total kWh, peak/off-peak, 30-minute load profile, export, reactive, etc.

### What this integration does today

**One Home Assistant device per service point.** The device's `manufacturer` / `model` / `serial_number` reflect the *currently installed* meter. Sensors are created per measurement channel that the API actually returns data for — when a channel starts being populated (e.g. `S-KWH-EXP` after a PV install, or `S-KWH-NORMAL`/`S-KWH-OFFPEAK` after switching to a two-tariff plan), new sensors appear on the next refresh without any reconfiguration.

This is a deliberate choice for **home automation**, not industrial-grade meter accounting:

- The Energy Dashboard wants a single, continuous consumption track per address. Splitting that across two devices (one for the old meter, one for the new) would force the user to template-sum or live with broken graphs at the swap boundary.
- The physical meter is metadata about *how* the data is collected; the address is what the user actually cares about ("how much electricity did this flat use?").
- A separate per-meter device model is still possible later — see "Open question" below.

### Counter resets when the meter is swapped

Cumulative kWh sensors are exposed with `state_class = total_increasing`, which is precisely Home Assistant's contract for "this is a monotonic counter that may reset to zero." When the DSO swaps the meter, the cumulative reading visibly jumps **down** (e.g. 79 304 kWh on the old ITRON → 0 kWh on the new Landis+Gyr). HA's recorder treats the drop as a reset, **not** as a negative consumption: long-term statistics keep accumulating the *delta*, so the lifetime total in the Energy Dashboard remains correct across the swap. No template, helper, or `utility_meter` workaround is required for this to be right.

What this *does not* preserve: the raw cumulative number on the old meter. It's available via the API and surfaced in entity attributes / diagnostics, but it isn't the headline state of any sensor.

### Open question

Whether `device = service point` or `device = physical meter` is the right model is **not settled**. The current shape is optimised for home automation; an installation that cares about per-meter accountability (audit, billing reconciliation, regulator reporting) would probably prefer a hierarchical model:

- Service point as the primary device,
- Each historical meter as a sub-device linked via `via_device`,
- Frozen sensors on retired meters carrying their final readings.

If you have a use case for the latter, please open an issue — happy to add it as a config-flow option without breaking the existing device layout.

## Development

The integration depends on the [`eac-dso-portal`](https://github.com/santafox/eac-dso-portal) library — bug reports about HTTP/parsing belong in that repo, bug reports about HA-side wiring (sensors, config flow, devices) belong here.

For deploying locally to a Home Assistant OS / Supervised host, `scripts/deploy_smb.sh` pushes the integration to a Samba share. It reads connection details from `.env` (gitignored — never committed):

```
HA_HOST=192.168.1.32
HA_SMB_USER=homeassistant
HA_SMB_PASS=...
```

After deploying, restart HA via the UI (Developer Tools → Restart) or `ha core restart` over SSH.

## License

MIT
