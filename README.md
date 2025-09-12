# Feelfit (Home Assistant Custom Integration)

Sync and monitor your **Feelfit body measurements** directly in Home Assistant.  
This custom integration connects to **Feelfit Cloud** using your Feelfit account and retrieves **weight, fat mass, muscle, water and more**, exposing them as **sensor entities** in HA.

[![Validate with HACS](https://img.shields.io/badge/HACS-validated-41BDF5)](https://hacs.xyz/)  
[![hassfest](https://img.shields.io/badge/hassfest-passing-brightgreen)](https://developers.home-assistant.io/docs/creating_integration_manifest/)  
[![MIT License](https://img.shields.io/badge/license-MIT-informational)](LICENSE.md)

> ⚠️ This is a third-party project, not affiliated with Feelfit.

---

## ✨ Features

- Login with your **Feelfit account (email/password)**.  
- Automatic discovery of your **linked Feelfit smart scales**.  
- Periodic cloud polling to retrieve:
  - User profile data (weight, height, birthday, etc.)  
  - Personal goals (weight, body fat, water, etc.)  
  - Device info (model, brand, MAC, etc.)  
  - Last body measurement (weight, body fat, muscle, water, bone mass, heart rate, etc.)  
- Each value is exposed as a **sensor entity**.  
- Coordinator-based architecture (auto-refresh every 30 seconds).  

---

## 🔧 Installation

### Option A — HACS (recommended)
1. Make sure you have [HACS](https://hacs.xyz/) installed in Home Assistant.
2. In Home Assistant: **HACS → Integrations → ⋮ (three dots) → Custom repositories**.  
   Add `https://github.com/Sanji78/feelfit` as **Category: Integration**.
3. Find **Feelfit** in HACS and click **Download**.
4. **Restart** Home Assistant.

### Option B — Manual
1. Copy the folder `custom_components/feelfit` from this repository into your Home Assistant config folder:
   - `<config>/custom_components/feelfit`
2. **Restart** Home Assistant.

---

## ⚙️ Configuration

### Step 1 — Add the Integration
1. In Home Assistant go to: **Settings → Devices & Services → Add Integration**.  
2. Search for **Feelfit**.  
3. Enter your **Feelfit account email and password**.  
4. On success, the integration will log in, store your access token and automatically fetch your profile, devices, goals, and last measurement.

---

## 📋 Entities

- **User profile sensors**
  - `account_name`, `weight`, `height`, `birthday`, `email`
- **Goals sensors**  
  - `weight`, `bodyfat`, `water` (more goal types are dynamically added if present)
- **Device sensors**  
  - One for each linked Feelfit scale, with detailed attributes (MAC, brand, model, WiFi name, etc.)
- **Last measurement sensors**
  - Weight, Body Fat, BMI, BMR, Metabolic Age, Muscle, Protein, Subcutaneous Fat, Visceral Fat, Hydration, Bone Mass, Heart Rate, Score, Measurement Timestamp, Body Water Mass, Protein Mass, Body Fat Mass  

All entities are automatically updated every 30 seconds via a shared coordinator.

---

## 🧪 Supported versions
- Home Assistant: **2024.8** or newer (earlier may work, untested).

---

## 🐞 Troubleshooting
- Check **Settings → System → Logs** for messages under `custom_components.feelfit`.  
- If login fails, double-check your **email and password**.  
- If no entities appear, try **removing and re-adding** the integration.

---

## 🙌 Contributing
PRs and issues are welcome. Please open an issue with logs if you hit a bug:  
[GitHub Issues](https://github.com/Sanji78/feelfit/issues)

---

## ❤️ Donate
If this project helps you, consider buying me a coffee:  
**[PayPal](https://www.paypal.me/elenacapasso80)**

..and yes... 😊 the paypal account is correct. Thank you so much!

---

## 📜 License
[MIT](LICENSE.md)
