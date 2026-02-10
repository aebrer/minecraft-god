# Shader & Graphics Setup Guide

How to make Minecraft Java Edition look incredible. This sets up:
- **Fabric** mod loader
- **Iris + Sodium** for shaders with great performance
- **Rethinking Voxels** shader pack (path-traced lighting — torches glow realistically!)
- **rotrBLOCKS** resource pack (PBR textures, 3D foliage, parallax)
- **MidnightControls** for controller support (optional)

Tested on Minecraft 1.21.11.

---

## Step 1: Install Fabric

1. Download the Fabric installer: https://fabricmc.net/use/installer/
2. Run it (double-click the .jar, or `java -jar fabric-installer.jar`)
3. Select **Client**, Minecraft version **1.21.11**, and click **Install**
4. A new "fabric-loader-1.21.11" profile will appear in your Minecraft Launcher

## Step 2: Download Mods

Download these 4 files and put them ALL in your `.minecraft/mods/` folder:

| Mod | Link | What it does |
|-----|------|-------------|
| **Sodium** | https://modrinth.com/mod/sodium | Massive FPS boost |
| **Iris Shaders** | https://modrinth.com/mod/iris | Shader support |
| **Fabric API** | https://modrinth.com/mod/fabric-api | Required library |
| **MidnightControls** | https://modrinth.com/mod/midnightcontrols | Controller support (optional) |

On each Modrinth page, click **Versions**, filter by **1.21.11** and **Fabric**, and download the .jar file.

**Where is the mods folder?**
- **Windows:** `%appdata%\.minecraft\mods\`  (paste this in File Explorer's address bar)
- **Mac:** `~/Library/Application Support/minecraft/mods/`
- **Linux:** `~/.minecraft/mods/`

Create the `mods` folder if it doesn't exist.

## Step 3: Download the Shader Pack

1. Download **Rethinking Voxels** from: https://modrinth.com/shader/rethinking-voxels
   - Click **Versions** and download the latest .zip
2. Put the .zip (don't unzip it!) in your `.minecraft/shaderpacks/` folder
   - **Windows:** `%appdata%\.minecraft\shaderpacks\`
   - Create the folder if it doesn't exist

## Step 4: Download the Resource Pack

1. Download **rotrBLOCKS** from: https://modrinth.com/resourcepack/rotrblocks
   - Click **Versions**, find the **128x** version for 1.21.11, download the .zip
2. Put the .zip (don't unzip it!) in your `.minecraft/resourcepacks/` folder
   - **Windows:** `%appdata%\.minecraft\resourcepacks\`

## Step 5: Launch and Configure

1. Open the Minecraft Launcher
2. Select the **fabric-loader-1.21.11** profile from the dropdown
3. Click **Play**
4. Once in the main menu:

**Enable the resource pack:**
- Go to **Options > Resource Packs**
- Move **rotrBLOCKS** to the right (active) side
- Make sure it's above the default pack
- Click **Done** (textures will reload)

**Enable the shader pack:**
- Go to **Options > Video Settings > Shader Packs**
- Select **Rethinking Voxels**
- Click **Apply** (may take a moment to compile)

**Adjust for performance:**
- If FPS is low, try turning down **Shadow Quality** and **Render Distance** in shader settings
- Medium settings look great and run well on most modern GPUs

## Step 6: Join the Server

- Add server: `your-server.example.com:25565`
- Or direct connect to `your-server.example.com`

---

**Troubleshooting:**
- **Purple/black textures?** Make sure rotrBLOCKS is above the default pack in resource pack list
- **Crash on launch?** Make sure you downloaded the correct versions — all mods must be for **1.21.11** and **Fabric**
- **Low FPS?** Turn down shadow quality in shader settings, or switch to Complementary Reimagined (lighter shader)
- **Controller not detected?** Check **Options > Controls > MidnightControls Settings**
