# EAC Cyprus, Home Assistant integration

Home Assistant integration for the EAC (Electricity Authority of Cyprus) Distribution Web Portal at `meterreading-dso.eac.com.cy`.

Pulls electricity-meter readings into HA so you can use them in the Energy Dashboard, in automations, or just to keep an eye on consumption. Built on top of the [`eac-dso-portal`](https://github.com/SantaFox/eac-dso-portal) Python library (also on [PyPI](https://pypi.org/project/eac-dso-portal/)).

## What you get

One Home Assistant device per active service point at your address, identified with the meter's manufacturer, model, and serial. Sensors are created per measurement channel that the API actually returns data for. Typical sensors:

- `Energy total (24h)`: cumulative kWh, ready for the Energy Dashboard.
- `Energy peak tariff` and `Energy off-peak tariff` for two-tariff plans.
- `Power import (30-min average)` and `Power export (30-min average)`: average power from the 30-minute load profile, expressed as kW so HA's sensor contract is satisfied (cumulative kWh for the same window is on the roadmap as an external statistic).
- `Energy exported`: populated automatically once you start exporting (PV install).
- Reactive and apparent energy channels: disabled by default.
- `Last successful update`: diagnostic timestamp so you can tell whether the integration is polling.

Sensors appear only for channels that the API actually returns data for, so the entity list stays tidy. New channels start appearing automatically when the meter starts reporting them.

On every poll the integration also pushes hourly long-term statistics for the cumulative kWh channels via `async_import_statistics`. This keeps the Energy Dashboard graph continuous: history is filled in from the API, not only from the moment you installed the integration.

## Configuration

After installation, open Settings, Devices & Services, the EAC integration tile, three-dot menu, Configure. You can change:

- Polling interval (30 minutes through 24 hours, default 6 hours).
- History window in days (1-60, default 14): how far back the integration pulls on every refresh. Larger values mean more API traffic per poll but also fill the Energy Dashboard graph further back.

The portal updates daily and 30-minute load-profile data lags by hours, so polling more often than 6 hours rarely buys anything.

## Install

### HACS

Submission to the HACS default repository is pending. In the meantime you can add this repo as a Custom Repository:

1. HACS, Integrations, three-dot menu, Custom repositories.
2. Repository: `https://github.com/SantaFox/ha-eac`, category Integration.
3. Add. Then install "EAC Distribution Web Portal" from the HACS list and restart HA.

### Manual

1. Copy `custom_components/eac_cyprus/` into your HA config's `custom_components/` folder. The simplest path is through the Samba add-on at `\\<HA-IP>\config\custom_components\`.
2. Restart Home Assistant.
3. Settings, Devices & Services, Add Integration, search for "EAC Distribution Web Portal".
4. Enter the email and password you use to sign in at `meterreading-dso.eac.com.cy`.

## Design notes: devices, service points, and meters

### How EAC structures the data

- A service point (`spId`, 12-digit) is a permanent supply contract tied to an address. It does not change.
- A meter is the physical hardware at that point. It can be replaced (mechanical to smart, faulty unit, etc.) and the API exposes the full history: `installDate`, `removalDate`, serial number, model.
- A meter can have multiple measurement channels (`mcList`) over its lifetime: total kWh, peak/off-peak, 30-minute load profile, export, reactive, apparent.

### What this integration does today

One Home Assistant device per service point. The device's `manufacturer`, `model`, and `serial_number` reflect the currently installed meter. Sensors are created per channel that the API actually returns data for, so when a channel starts being populated (for example `S-KWH-EXP` after a PV install, or `S-KWH-NORMAL`/`S-KWH-OFFPEAK` after switching to a two-tariff plan), new sensors appear on the next refresh without any reconfiguration.

This is a deliberate choice for home automation, not industrial meter accounting:

- The Energy Dashboard wants a single, continuous consumption track per address. Splitting that across two devices (one for the old meter, one for the new) would force the user to template-sum or live with broken graphs at the swap boundary.
- The physical meter is metadata about how the data is collected; the address is what the user actually cares about ("how much electricity did this flat use?").
- A separate per-meter device model is still possible later, see "Open question" below.

### Counter resets when the meter is swapped

Cumulative kWh sensors use `state_class = total_increasing`, which is Home Assistant's contract for "this is a monotonic counter that may reset to zero." When the DSO swaps the meter, the cumulative reading visibly jumps down (for example 79 304 kWh on the old ITRON to 0 kWh on the new Landis+Gyr). HA's recorder treats the drop as a reset, not as a negative consumption: long-term statistics keep accumulating the delta, so the lifetime total in the Energy Dashboard remains correct across the swap. No template, helper, or `utility_meter` workaround is required.

What this does not preserve: the raw cumulative number on the old meter. It stays available via the API and is surfaced in entity attributes, but it isn't the headline state of any sensor.

### Open question

Whether `device = service point` or `device = physical meter` is the right model is not settled. The current shape is optimised for home automation. An installation that cares about per-meter accountability (audit, billing reconciliation, regulator reporting) would probably prefer a hierarchical model:

- Service point as the primary device.
- Each historical meter as a sub-device linked via `via_device`.
- Frozen sensors on retired meters carrying their final readings.

If you have a use case for the latter, please open an issue. Happy to add it as a config-flow option without breaking the existing device layout.

## Development

The integration depends on the [`eac-dso-portal`](https://github.com/SantaFox/eac-dso-portal) library. Bug reports about HTTP and parsing belong in that repo; bug reports about HA-side wiring (sensors, config flow, devices) belong here.

For deploying locally to a Home Assistant OS or Supervised host, `scripts/deploy_smb.sh` pushes the integration to a Samba share. It reads connection details from `.env`, which is gitignored:

```
HA_HOST=192.168.1.32
HA_SMB_USER=homeassistant
HA_SMB_PASS=...
```

After deploying, restart HA via the UI (Developer Tools, Restart) or `ha core restart` over SSH.

## License

MIT
