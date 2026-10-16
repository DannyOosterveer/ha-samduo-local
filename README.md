# SAMDUO Nex for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Tests](https://github.com/DannyOosterveer/ha-samduo-local/actions/workflows/tests.yml/badge.svg)](https://github.com/DannyOosterveer/ha-samduo-local/actions/workflows/tests.yml)
[![GitHub release](https://img.shields.io/github/release/DannyOosterveer/ha-samduo-local.svg)](https://github.com/DannyOosterveer/ha-samduo-local/releases)
![Maintained](https://img.shields.io/badge/maintained-yes-brightgreen.svg)
[![License](https://img.shields.io/github/license/DannyOosterveer/ha-samduo-local.svg)](LICENSE)

Local, cloud-free control of the **SAMDUO Nex E6000** plug-in home battery, with
eleven built-in charging strategies: from plain *zero on the meter* to trading on
dynamic energy prices. Everything runs inside the integration; no YAML packages,
no helper automations, no Node-RED.

The strategies and the dashboard follow the well-known
[Zendure zenSDK package by Gielz1986](https://github.com/Gielz1986/Zendure-HA-zenSDK),
so if you come from there you will find the same modes and the same knobs.

> **Status: early.** Built against the SAMDUO Open API Protocol V1.0 and tested
> on a Nex E6000 (firmware 0.0.1.10). Other Nex models speak the same protocol per
> the spec but are untested.

## Supported devices

| Device | Status |
|---|---|
| SAMDUO Nex E6000 | ✅ Tested |
| SAMDUO Nex E6000H | Untested, same protocol per spec |
| SAMDUO Nex P2800 Pro | Untested, same protocol per spec |
| SAMDUO Nex BP2800 | Untested, same protocol per spec |

## Requirements

- Home Assistant 2025.3 or newer.
- The battery on the same LAN as Home Assistant (TCP port 3335).
- **"HEMS Managed" switched OFF in the SAMDUO app.** With it on, the battery
  regulates itself on its own P1 meter and silently ignores every command. The
  integration detects this and raises a repair issue.
- For the zero-on-the-meter modes: a power sensor at your meter in watts,
  positive = import, negative = export. A [HomeWizard P1](https://www.home-assistant.io/integrations/homewizard/)
  or any fast P1/CT sensor works.
- For the dynamic modes: a price sensor with today's and tomorrow's prices in its
  attributes, such as [Nordpool](https://github.com/custom-components/nordpool),
  [ENTSO-e](https://github.com/JaccoR/hass-entso-e) or EnergyZero.

⚠️ The battery answers **one TCP connection at a time**. If the sensors stop
updating while the SAMDUO app is open, close the app; the integration reconnects
on its own.

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=DannyOosterveer&repository=ha-samduo-local&category=integration)

Or manually: HACS → Integrations → ⋮ → Custom repositories → add
`https://github.com/DannyOosterveer/ha-samduo-local` as category *Integration*, install
**SAMDUO Nex** and restart Home Assistant.

### Manual

Copy `custom_components/samduo` into your `config/custom_components/` folder and
restart Home Assistant.

## Setup

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=samduo)

Batteries on the LAN are discovered automatically (mDNS) and show up under
**Settings → Devices & services**. Confirm the discovered battery and give it a
name; the name becomes the prefix of all entity ids (`Samduo` → `sensor.samduo_…`).
Manual setup by IP address is available as a fallback.

Then open **Configure** on the integration and pick:

| Option | What it is for |
|---|---|
| Grid power sensor (P1) | Needed for every zero-on-the-meter mode |
| Energy price sensor | Needed for the dynamic modes |
| Battery capacity | Available/needed energy and remaining-time sensors (default 6 kWh) |
| Poll interval | How often the battery is read and the strategy runs (default 5 s) |
| Setpoint refresh / watchdog timeout | See [Safety](#safety) |

Press **Apply recommended settings** once to load sensible defaults (−300 W to
start charging, 100 W to start discharging, margins of 50 W / 5 W, SOC 10–100 %,
minimum spread 25 %).

## Modes

Pick a mode with the **Mode** select. The **Strategy** sensor tells you what the
engine is doing right now (for example *Discharging to zero on the meter*).

| Mode | What the battery does |
|---|---|
| **Standby** | Nothing. Control is released; the battery is left alone. |
| **Manual** | Holds the **Manual power** value. Positive = discharge, negative = charge. |
| **Zero on the meter** | Discharges when the house imports more than *Start discharging at*, charges when it exports more than *Start charging at*, and balances the meter to the margins. |
| **Dynamic NOM** | Charges from the grid in the cheapest periods (once per cheap block, again only after the SOC dropped 10 % below the maximum), otherwise zero on the meter. Charging requires the *Spread NOM* to be at least *Minimum spread*. |
| **Dynamic NOM (expensive only)** | Charges in cheap periods; discharges to zero on the meter only during expensive periods; absorbs surplus any time. Idle otherwise. |
| **Dynamic trading** | Charges at full power in cheap periods, discharges at full power in expensive periods. Idle in between. |
| **Dynamic trading + NOM** | Same as trading, but zero on the meter in between. |
| **Smart discharge only** | Zero on the meter, discharging only. |
| **Smart charge only** | Zero on the meter, charging surplus only. |
| **Fast charge** | Full charge power until the maximum SOC. |
| **Fast discharge** | Full discharge power until the minimum SOC. |

Every mode respects the **Minimum SOC** and **Maximum SOC**. With **SOC protection**
on, the battery is topped up to the minimum SOC from the grid when it drops below
it (in daylight, or whenever the house is exporting anyway).

### How zero on the meter works

The first step towards a new target takes 75 % of the demand; from then on the
setpoint is balanced every poll at 100 %. The *Discharge margin* makes the battery
deliver a little more than the house needs (so the meter sits just below zero);
the *Charge margin* leaves a little export on the meter while charging. A new
direction starts only after a 10 s dwell, and only beyond the start thresholds,
so meter noise does not make the battery flap.

### Dynamic prices

With a price sensor configured, the integration picks the *N cheapest* and
*M most expensive* periods of the day (settings for today and tomorrow; tomorrow's
values roll over at midnight). Periods are hourly by default; switch
**15-minute periods** on for quarter-hourly prices.

- **Spread** = (average expensive − export correction − average cheap) / average cheap, in %.
- **Spread NOM** uses only the expensive periods *after* the first cheap one: energy
  bought at night is only worth the prices it displaces later that day.
- **Manual periods** override the automatic selection for a day. Format:
  `G02:00;G03:00;D18:00-21:00` (G/C = cheap, D/E = expensive, single time = one
  period, range = every period in it).

The **Price periods today/tomorrow** sensors carry the full period list, with
cheap/expensive flags, in their attributes; the dashboard charts are drawn from
them.

## Entities

Telemetry (every 5 s): battery SOC, battery power, battery status, backup power,
energy charged / discharged (`total_increasing`, Energy Dashboard ready), grid
voltage / frequency, off-grid voltage, battery voltage, SOH, two inverter
temperatures, inverter status, error code, control time remaining.

Strategy: strategy state, power setpoint, grid power, house consumption,
available energy, energy to full, remaining charge / discharge time, current
price, spread (today / tomorrow, plain / NOM), price periods (today / tomorrow),
cheap / expensive period flags, controlling, dynamic charging / discharging,
grid sensor available, control blocked.

Settings (restored after a restart): mode, manual power, max charge / discharge
power, start charging / discharging at, charge / discharge margin, minimum /
maximum SOC, minimum spread, export correction, cheap / expensive periods (today
and tomorrow), manual periods (today and tomorrow), SOC protection, 15-minute
periods. Plus the backup output switch, and the *Release control* and *Apply
recommended settings* buttons.

### Sign convention

Battery power and setpoints are **positive = discharging, negative = charging**,
the battery's own convention (and what EMHASS expects). Grid power is positive =
import. Values are never flipped; mind the sign when you combine them with other
integrations.

## Dashboard

[`dashboard/samduo_dashboard.yaml`](dashboard/samduo_dashboard.yaml) is a complete
plug-and-play dashboard: battery, control settings, dynamic prices, and a health
view. It needs the [apexcharts-card](https://github.com/RomRider/apexcharts-card)
from HACS.

1. Settings → Dashboards → **Add dashboard** → *New dashboard from scratch*.
2. Open it → ⋮ → **Edit dashboard** → ⋮ → **Raw configuration editor**.
3. Paste the file and save.

The entity ids assume the battery is named *Samduo*. If you chose another name,
replace `samduo_` with your name.

## Safety

Every setpoint is written with a watchdog (default 90 s) and refreshed every
30 s. If Home Assistant crashes or loses the network, the battery frees itself
within the watchdog window; no stuck setpoints by design. **Release control**
(or selecting *Standby*) writes a short 0 W hold and stops the refresh.

The zero-on-the-meter modes never steer blind: when the grid power sensor is
unavailable, or has not reported for two minutes, the battery is set to 0 W until
the sensor is back.

The protocol has no device-side validation, so charge and discharge commands are
clamped to the **Max charge power** / **Max discharge power** settings (default
800 W, the grid-compliant figure; the E6000 inverter is rated 2600 W). Keep these
at or below the limit set in the SAMDUO app.

## Development

```bash
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements_test.txt
.venv/bin/python -m pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

The protocol layer, the price logic and the strategy engine are pure Python with
no Home Assistant dependency and are tested with hand-written readings; the
integration itself is tested with the Home Assistant test framework and a mocked
battery, and the client against a fake battery on a real socket.

## Acknowledgements

- SAMDUO, for publishing the Open API Protocol.
- [Gielz1986](https://github.com/Gielz1986/Zendure-HA-zenSDK) for the strategy and
  dashboard design the modes are modelled on.
- [StekkerDeal/samduo-local](https://github.com/StekkerDeal/samduo-local), whose
  protocol reconnaissance (one service per message, mandatory ACK, bare JSON
  framing, per-mille SOC) saved a lot of trial and error.

## License

MIT
