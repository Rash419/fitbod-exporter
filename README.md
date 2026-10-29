# Fitbod Android Exporter

Export your own Fitbod workout history to JSON and CSV through an authorized
Android USB connection. It fetches history in bulk through Fitbod's private API;
it does not open each workout screen.

The Android helper runs using the existing `app_process` runtime over ADB.
It needs neither root nor an installed helper app. Authentication uses a session
from your successful official Fitbod website login. The session travels to the
phone over stdin and stays in memory; it is never placed in shell arguments.

Desktop requests can be rejected by Cloudflare even when authentication works.
Android-native HTTPS worked in the tested setup. This is an undocumented API
and compatibility may change; the exact reason for different Cloudflare
behavior was not established.

## Requirements

- Python 3.10 or newer, using only the standard library.
- JDK 17 or newer, with `java` and `javac` on PATH or `JAVA_HOME` configured.
- Android SDK platform-tools, an installed Android platform, and build-tools
  containing `lib/d8.jar`.
- Android 8.0 or newer with Fitbod installed and USB debugging authorized.
- A successful login to your own Fitbod account.

The workflow was tested on Linux with Android 16 and Fitbod 8.28.0-2.

## Setup

```bash
git clone https://github.com/Rash419/fitbod-exporter.git
cd fitbod-exporter
```

Set `ANDROID_SDK_ROOT` or `ANDROID_HOME` to your SDK directory. Otherwise the
scripts check `~/Android/Sdk`. `adb` can also be found on PATH. The native
exporter accepts `--sdk-root`, `--jdk-bin`, and `--serial` overrides.

Connect and unlock your phone, enable Developer options and USB debugging,
and accept its USB debugging authorization prompt. Check that `adb devices`
shows the device as authorized. With multiple connected devices, set
`ANDROID_SERIAL` or use `--serial` to select one.

## 1. Extract stock exercise names

```bash
python extract_exercise_names.py
```

This copies the installed Fitbod APK to `private/` and reads its bundled
exercise-name table. You can instead supply an APK you already copied:

```bash
python extract_exercise_names.py --apk /path/to/your/fitbod.apk
```

The proprietary exercise catalogue and APK are kept local and are not
redistributed in this repository.

## 2. Capture an authenticated session

In your regular browser:

1. Open https://app.fitbod.me/login and open Developer Tools before logging in.
2. Select Network, enable Preserve log, then complete your normal Fitbod login.
3. In DevTools Settings > Preferences > Network, enable HAR export with sensitive data.
4. Export the network log as a HAR with sensitive data and save it locally as
   `private/fitbod-login.har`.

Chrome documents the option in its
[Network reference](https://developer.chrome.com/docs/devtools/network/reference/).
The HAR may contain passwords, cookies and session tokens. Keep it local;
do not upload it to GitHub or attach it to issue reports.

Capture the official login's refresh cookie without making desktop API calls:

```bash
python import_fitbod_har.py private/fitbod-login.har --capture-only
```

The session is saved in `private/refresh-token.txt` with owner-only permissions.
Capture alone does not validate it; token exchange on Android does that.
Alternatively, an existing refresh token can be entered at a hidden prompt:

```bash
python fitbod_export.py --import-token
```

## 3. Fetch workouts and custom exercises

```bash
python android_export.py
python android_export.py --catalogue-only
```

The commands print their timestamped output directories under `private/`.
History is paginated until an empty page, with duplicate-page detection and
record-count validation. Custom exercise names are fetched separately.

The helper only contacts `nautilus.fitbod.me` over HTTPS with normal certificate
validation and redirects disabled. Token exchange is the only POST; history
and catalogue calls are GETs. Temporary phone files are copied locally and
removed after successful transfer. A failed transfer may leave its temporary
`/data/local/tmp/fitbod-native-*` directory for recovery.

For compilation without connecting a phone:

```bash
python android_export.py --build-only
```

## 4. Create readable exports

Substitute the actual source paths printed by the previous commands:

```bash
python finalize_export.py private/android-export-TIMESTAMP \
  --catalogue private/android-catalogue-TIMESTAMP/custom-exercises.json \
  --timezone UTC
```

The timezone accepts an IANA name such as `Europe/London` or `Asia/Kolkata`.
It defaults to UTC and can also be set with `FITBOD_TIMEZONE`.

Final files are written under `private/workout-export/`:

| File | Contents |
| --- | --- |
| `fitbod-nonrunning-sets.csv` | Every retained set from non-running sessions |
| `fitbod-strength-sets.csv` | Non-running Fitbod-authored sessions (`source_id == 0`) |
| `fitbod-workouts.csv` | One row per non-running workout |
| `fitbod-workouts.json` | Original non-running records, including deletion markers |
| `export-summary.json` | Counts, dates and scope |
| `README.txt` | Export field notes |

A data-only ZIP is created at `private/fitbod-workout-export.zip`.

CSV includes exercise names, reps, weights, warmup flags, rest times, notes and
original IDs. Sets marked `deletedAt`, deleted exercises and deleted circuits
are omitted from CSV; warmups are retained. Running-only sessions are identified
using the exercise catalogue. Mixed sessions remain intact. The strength file
uses the API source identifier, rather than independently classifying movements.

Weights are labelled kg based on the inspected contract and sampled app sets.
Workout durations and rest times are seconds; set durations preserve their raw
source representation. Original timestamps are retained alongside converted
local timestamps. Raw API pages keep all fetched records for auditing. Backend
counts can differ from the app's displayed counter.

## Other tools

- `fitbod_export.py`: desktop HTTP export and hidden credential/session prompts.
  Login can read `FITBOD_EMAIL` and `FITBOD_PASSWORD` from the environment or
  a local `.env`; see `.env.example`. Desktop requests may be blocked.
- `import_fitbod_har.py`: local HAR parsing. Without `--capture-only`, it attempts
  to validate candidate tokens through the desktop connection.
- `phone_reader.py`: optional exploratory accessibility reader. This is not used
  by the bulk-export workflow.

## Tests

```bash
python -m unittest discover -v
```

The tests use synthetic fixtures and do not contact Fitbod or a phone.
They cover pagination, circuit/warmup preservation, deletion markers, request
format, transfer counts and CSV formula escaping.

## Local data and attribution

Credentials, recordings, APKs, compiled helpers and exported workout data are
ignored by Git. Never force-add files from `private/`. Error diagnostics may
contain private response details and should be reviewed before sharing.

The API contract and authentication flow were informed by
[Muno459/mcp-fitbod](https://github.com/Muno459/mcp-fitbod); its MIT notice is
reproduced in `THIRD_PARTY_NOTICES.md`. No downloaded upstream code is executed
by these scripts. This project is unaffiliated with Fitbod.
