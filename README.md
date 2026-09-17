# Advanced Dual Display Auto-Brightness Engine

A highly optimized, multi-threaded Windows application that dynamically synchronizes dual-monitor backlights to your room's ambient light in real-time. Built to handle the intricacies of modern display hardware, it bypasses the jarring, sudden brightness jumps of standard tools in favor of a butter-smooth, hardware-safe fading engine.

---

## 🌟 Core Features

### Intelligent Ambient Sensing

Uses your webcam to continuously monitor room lighting. It specifically analyzes the top 30% of the camera frame to capture ceiling/window light, actively ignoring user movement or dark clothing.

### Hardware-Safe Fader Engine

Prevents I2C bus saturation (flickering/crashes) by micro-stepping brightness at exactly 1% every 80ms.

### Smart Protocol Routing

Automatically detects and routes commands via:

- **VCP (DDC/CI)** for external hardware monitors
- **WMI (Intel HD Graphics Color Intensity)** for laptop displays or unsupported hardware

### 5-Second Moving Average

Eliminates erratic brightness changes when clouds pass or shadows move by utilizing a fast-polling (0.5s) deque buffer.

### Interpolation Matrix Profiles

Allows users to map exact display brightness percentages to specific ambient light conditions, math-calculating the perfect intermediate brightness automatically.

### Zero-Dependency Offline Installer

Packaged as a fully standalone `.msi` Windows Installer. No Python environment or internet connection required for end users.

---

# 📖 For Users: Installation & Guide

## 1. Installation

Simply download the provided `AutoBrightness_Engine-1.0-amd64.msi` and run it.

The installer will:

- Extract all necessary dependencies
- Create a Start Menu shortcut
- Optionally launch the app

### No Python Required

The app is 100% self-contained.

### Data Safe

Configuration and logs are safely stored in your Windows user directory:

```text
%LOCALAPPDATA%\AutoBrightness
```

---

## 2. Using the Application

When running, the app lives in your System Tray. Double-click the sun logo to open the dashboard.

### Telemetry Dashboard

View live feeds of:

- What your camera sensor is reading
- Your current hardware protocol status
- The active targets for both monitors

### Brightness Step Profiles

This is where you tune the app to your room.

#### Sense

Click **Sense** to read the room's current lighting.

#### Set

Click **Set** to open live sliders. Adjust both monitors until your eyes are comfortable, then hit **Apply**.

#### Save

Click **Save** to lock in that lighting profile.

> **Note:** Ensure at least two profiles are checked as **Active** so the engine can calculate the transitions between them.

### Engine Settings

Configure the app to start quietly in the System Tray on Windows boot.

---

# 🛠 For Developers: Architecture & Deep Dive

If you are modifying the source code, this app employs a strict decoupled multi-threading architecture to keep the Tkinter UI responsive while performing heavy I/O operations.

## The Threading Model

### 1. UI Main Loop

Standard Tkinter thread.

Reads telemetry payloads from a thread-safe `queue.Queue` every 200ms to update the dashboard.

### 2. Continuous Sensor Engine (`core_sensor_engine`)

Polls the webcam every 0.5s.

#### The "Dark Shirt" Fix

Crops the ROI (Region of Interest) to:

```python
frame[0:int(h * 0.30), 0:w]
```

This samples only upper room lighting and reduces interference from user movement or dark clothing.

#### Auto-Exposure Lock

Forces:

```python
CAP_PROP_AUTO_EXPOSURE = 0.25
```

This prevents the camera's internal firmware from over-compensating in bright sunlight.

#### Moving Average

Maintains:

```python
collections.deque(maxlen=10)
```

With a 0.5s polling interval, this represents approximately 5 seconds of sensor history.

The average of these samples is used to smooth the ambient-light measurement.

### 3. Smooth Fader Engine (`smooth_fader_engine`)

Hardware I2C buses (DDC/CI) are notoriously slow and can drop packets or crash if overwhelmed.

This thread strictly applies a:

```text
+= 1
```

or:

```text
-= 1
```

change and then sleeps for:

```text
80ms
```

This creates a visually smooth fade while keeping hardware traffic below saturation thresholds.

A theoretical 0% to 100% transition takes:

```text
100 × 80ms = 8000ms
```

or approximately **8 seconds**.

---

# 🔌 Protocol Handling

The app utilizes `screen-brightness-control` but manually handles routing to prevent cross-talk.

When mixing a DDC monitor and a WMI (laptop) display, passing standard integer indexes such as:

```python
display=0
```

can confuse the routing table.

We bypass this by explicitly requesting the hardware UUIDs/names via:

```python
sbc.list_monitors()
```

and directing the 1% step commands explicitly to those strings.

This ensures that brightness commands are routed to the intended display.

---

# 🏗 Building from Source

## Prerequisites

You will need:

- Python 3.10+
- Python 3.13 supported and recommended
- Windows

---

## 1. Clone and Set Up the Environment

```bash
git clone https://github.com/yourusername/auto-brightness-engine.git
cd auto-brightness-engine

python -m venv venv
venv\Scripts\activate
```

---

## 2. Install Dependencies

```bash
python -m pip install screen-brightness-control pystray opencv-python Pillow cx_Freeze
```

---

## 3. Build the MSI Installer

```bash
python setup.py bdist_msi
```

---

# ⚙️ Compiler Optimizations (`setup.py`)

We utilize **cx_Freeze** over PyInstaller for native `.msi` generation.

## `zip_include_packages`

```python
"zip_include_packages": ["*"]
```

This is critical because OpenCV contains thousands of loose files.

Without this, the MSI installer can take 5+ minutes to write files one by one.

With it, packages are placed into a single `library.zip` archive, making installation nearly instantaneous.

## `optimize`

```python
"optimize": 2
```

This optimization level reduces unnecessary Python bytecode metadata and helps reduce the final package size.

---

# 📁 Application Data

Configuration and logs are stored in:

```text
%LOCALAPPDATA%\AutoBrightness
```

This keeps application data separate from the installation directory.

---

# 🧵 Architecture Overview

The application is divided into three primary components:

```text
                         ┌─────────────────────┐
                         │     Webcam Sensor   │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ core_sensor_engine  │
                         │                     │
                         │ • 0.5s polling     │
                         │ • ROI processing    │
                         │ • Exposure control  │
                         │ • Moving average    │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ Profile / Target    │
                         │    Calculation      │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ smooth_fader_engine │
                         │                     │
                         │ • ±1% steps         │
                         │ • 80ms interval     │
                         │ • Hardware routing  │
                         └──────────┬──────────┘
                                    │
                       ┌────────────┴────────────┐
                       ▼                         ▼
             ┌──────────────────┐       ┌──────────────────┐
             │    DDC / VCP     │       │       WMI        │
             │ External Monitor │       │ Laptop / Other   │
             └──────────────────┘       └──────────────────┘
```

The Tkinter UI communicates with the background engines through a thread-safe queue:

```text
┌─────────────────┐
│   Tkinter UI    │
└────────┬────────┘
         │
         │ queue.Queue
         ▼
┌─────────────────┐
│ Telemetry Data  │
└─────────────────┘
```

This prevents long-running hardware and camera operations from blocking the graphical interface.

---

# 🔒 Hardware-Safety Philosophy

The brightness engine intentionally avoids sending large numbers of rapid brightness commands.

Instead of immediately changing:

```text
20% → 80%
```

the engine performs:

```text
20%
21%
22%
23%
...
79%
80%
```

with an **80ms delay between steps**.

This results in:

- Smoother visual transitions
- Reduced DDC/CI traffic
- Lower probability of communication failures
- Less aggressive interaction with monitor firmware
- Better synchronization between multiple displays

---

# 📊 Example Interpolation

Suppose two active profiles are configured:

| Ambient Light | Monitor Brightness |
|---:|---:|
| 100 | 20% |
| 500 | 50% |

If the measured ambient light is **300**, the engine interpolates between the two profiles.

For a linear interpolation:

```text
Ambient = 300

Brightness ≈ 35%
```

The calculated target is then passed to the smooth fader engine rather than being applied instantaneously.

---

# 🔄 Runtime Flow

The application follows this general runtime sequence:

```text
Application Start
       │
       ▼
Initialize Configuration
       │
       ▼
Detect Displays
       │
       ▼
Identify Brightness Protocol
       │
       ├───────────────┐
       ▼               ▼
     VCP              WMI
       │               │
       └───────┬───────┘
               ▼
        Start Sensor Thread
               │
               ▼
        Read Webcam @ 0.5s
               │
               ▼
          Crop Top 30%
               │
               ▼
       Calculate Light Level
               │
               ▼
       Update 5s Moving Avg.
               │
               ▼
       Calculate Target %
               │
               ▼
       Smooth Fader Thread
               │
               ▼
          ±1% Every 80ms
               │
               ▼
       Update Monitor(s)
               │
               └───────────────┐
                               │
                               ▼
                         Repeat Forever
```

---

# 🖥️ Display Control Flow

The brightness-control process can be summarized as:

```text
                    Ambient Light
                         │
                         ▼
                   Webcam Frame
                         │
                         ▼
                    Top 30% ROI
                         │
                         ▼
                  Light Calculation
                         │
                         ▼
                  5-Second Average
                         │
                         ▼
                Active Profile Range
                         │
                         ▼
                   Interpolation
                         │
                         ▼
                  Target Brightness
                         │
                         ▼
                 Smooth Fader Engine
                         │
                    ┌────┴────┐
                    ▼         ▼
                  DDC/CI     WMI
                    │         │
                    ▼         ▼
                Monitor 1  Monitor 2
```

---

# 📈 Brightness Transition Example

If the current brightness is 30% and the calculated target is 70%, the application does not immediately send:

```text
30% → 70%
```

Instead, it performs:

```text
30%
31%
32%
33%
34%
...
68%
69%
70%
```

with an 80ms interval between steps.

This means:

```text
40 steps × 80ms = 3200ms
```

So the transition takes approximately:

**3.2 seconds**

---

# 🎯 Design Goals

The application is designed around five primary goals:

1. **Smoothness**  
   Brightness changes should never feel abrupt.

2. **Hardware Safety**  
   Avoid excessive DDC/CI communication.

3. **Responsiveness**  
   Camera and monitor operations must never freeze the UI.

4. **Accuracy**  
   Ambient light should be translated into predictable brightness targets.

5. **Offline Operation**  
   Once installed, the application should not require an internet connection.

---

# 📝 License & Copyright

Copyright (c) 2026 Ayush Raj

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to:

- Use
- Copy
- Modify
- Merge
- Publish
- Distribute
- Sublicense
- Sell copies of the Software

Subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.

IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES, OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
