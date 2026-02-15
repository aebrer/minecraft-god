package ca.entropist.minecraftgod;

import com.google.gson.*;
import net.sandrohc.schematic4j.SchematicLoader;
import net.sandrohc.schematic4j.schematic.Schematic;
import org.bukkit.*;
import org.bukkit.Tag;
import org.bukkit.block.Block;
import org.bukkit.block.data.BlockData;
import org.bukkit.entity.Entity;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.block.BlockBreakEvent;
import org.bukkit.event.block.BlockPlaceEvent;
import org.bukkit.event.entity.EntityDamageByEntityEvent;
import org.bukkit.event.entity.EntityDeathEvent;
import org.bukkit.event.player.AsyncPlayerChatEvent;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.event.weather.ThunderChangeEvent;
import org.bukkit.event.weather.WeatherChangeEvent;
import org.bukkit.plugin.java.JavaPlugin;
import org.bukkit.scheduler.BukkitRunnable;

import java.io.File;
import java.util.LinkedHashMap;
import java.util.Map;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.ConcurrentLinkedQueue;

/**
 * Minecraft God — Paper Plugin
 *
 * Listens to game events and forwards them to the Python backend via HTTP.
 * Polls for commands from the backend and executes them in-game.
 *
 * Event listeners and HTTP bridge for the Paper server.
 */
@SuppressWarnings("deprecation") // AsyncPlayerChatEvent — still functional, simpler than Adventure chat API
public class MinecraftGodPlugin extends JavaPlugin implements Listener {

    private static final String BACKEND_URL = "http://localhost:8000";
    private static final int CLOSE_RANGE = 8;
    private static final int BLOCK_SCAN_RANGE = 8;

    /** Directory containing .schem files for divine construction. */
    private File schematicsDir;

    /** Stack of recent builds for undo. Each entry records the original block states. */
    private static final int MAX_UNDO_HISTORY = 5;
    private final Deque<BuildSnapshot> buildHistory = new ArrayDeque<>();

    /** Snapshot of blocks before a schematic was placed, for undo. */
    private record BuildSnapshot(String blueprintId, World world, List<BlockPlacement> originalBlocks, long timestamp) {}

    /** Blocks worth reporting in the nearby scan — ores, containers, hazards, structures. */
    private static final Set<org.bukkit.Material> NOTABLE_BLOCKS = EnumSet.of(
            // Ores
            org.bukkit.Material.COAL_ORE, org.bukkit.Material.IRON_ORE,
            org.bukkit.Material.GOLD_ORE, org.bukkit.Material.DIAMOND_ORE,
            org.bukkit.Material.EMERALD_ORE, org.bukkit.Material.LAPIS_ORE,
            org.bukkit.Material.REDSTONE_ORE, org.bukkit.Material.COPPER_ORE,
            org.bukkit.Material.DEEPSLATE_COAL_ORE, org.bukkit.Material.DEEPSLATE_IRON_ORE,
            org.bukkit.Material.DEEPSLATE_GOLD_ORE, org.bukkit.Material.DEEPSLATE_DIAMOND_ORE,
            org.bukkit.Material.DEEPSLATE_EMERALD_ORE, org.bukkit.Material.DEEPSLATE_LAPIS_ORE,
            org.bukkit.Material.DEEPSLATE_REDSTONE_ORE, org.bukkit.Material.DEEPSLATE_COPPER_ORE,
            org.bukkit.Material.NETHER_GOLD_ORE, org.bukkit.Material.NETHER_QUARTZ_ORE,
            org.bukkit.Material.ANCIENT_DEBRIS,
            // Containers
            org.bukkit.Material.CHEST, org.bukkit.Material.TRAPPED_CHEST,
            org.bukkit.Material.BARREL, org.bukkit.Material.ENDER_CHEST,
            org.bukkit.Material.SHULKER_BOX,
            // Utility
            org.bukkit.Material.FURNACE, org.bukkit.Material.BLAST_FURNACE,
            org.bukkit.Material.SMOKER, org.bukkit.Material.BREWING_STAND,
            org.bukkit.Material.CRAFTING_TABLE, org.bukkit.Material.ANVIL,
            org.bukkit.Material.ENCHANTING_TABLE, org.bukkit.Material.GRINDSTONE,
            org.bukkit.Material.SMITHING_TABLE, org.bukkit.Material.STONECUTTER,
            org.bukkit.Material.CARTOGRAPHY_TABLE, org.bukkit.Material.LOOM,
            org.bukkit.Material.HOPPER, org.bukkit.Material.DISPENSER, org.bukkit.Material.DROPPER,
            org.bukkit.Material.BEACON, org.bukkit.Material.RESPAWN_ANCHOR,
            // Hazards
            org.bukkit.Material.LAVA, org.bukkit.Material.FIRE, org.bukkit.Material.SOUL_FIRE,
            org.bukkit.Material.MAGMA_BLOCK, org.bukkit.Material.CACTUS,
            org.bukkit.Material.SWEET_BERRY_BUSH, org.bukkit.Material.POWDER_SNOW,
            org.bukkit.Material.TNT,
            // Structures / special
            org.bukkit.Material.SPAWNER, org.bukkit.Material.END_PORTAL_FRAME,
            org.bukkit.Material.END_PORTAL, org.bukkit.Material.NETHER_PORTAL,
            org.bukkit.Material.OBSIDIAN, org.bukkit.Material.CRYING_OBSIDIAN,
            org.bukkit.Material.BEDROCK, org.bukkit.Material.BUDDING_AMETHYST,
            // Nature / farming
            org.bukkit.Material.BEE_NEST, org.bukkit.Material.BEEHIVE,
            org.bukkit.Material.WATER
    );

    private final HttpClient httpClient = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(5))
            .build();
    private final Gson gson = new Gson();

    @Override
    public void onEnable() {
        getServer().getPluginManager().registerEvents(this, this);

        // Locate schematics directory (relative to server working directory: ../scripts/schematics/schematics/)
        File serverRoot = new File(System.getProperty("user.dir")); // paper/ directory (absolute)
        schematicsDir = new File(serverRoot.getParentFile(), "scripts/schematics/schematics");
        if (schematicsDir.isDirectory()) {
            int count = 0;
            File[] files = schematicsDir.listFiles((dir, name) -> name.endsWith(".schem"));
            if (files != null) count = files.length;
            getLogger().info("Schematic library loaded: " + count + " blueprints in " + schematicsDir.getAbsolutePath());
        } else {
            getLogger().warning("Schematics directory not found: " + schematicsDir.getAbsolutePath());
        }

        // Command polling — async, every 100 ticks (5 seconds)
        new BukkitRunnable() {
            @Override
            public void run() {
                pollCommands();
            }
        }.runTaskTimerAsynchronously(this, 100, 100);

        // Player status beacon — sync (reads player data), every 600 ticks (30 seconds)
        new BukkitRunnable() {
            @Override
            public void run() {
                sendPlayerStatus();
            }
        }.runTaskTimer(this, 600, 600);

        // Register /godundo command
        if (getCommand("godundo") != null) {
            getCommand("godundo").setExecutor(this::onGodUndoCommand);
        }

        getLogger().info("The gods are watching.");
    }

    @Override
    public void onDisable() {
        getLogger().info("The gods have departed.");
    }

    private boolean onGodUndoCommand(org.bukkit.command.CommandSender sender,
                                      org.bukkit.command.Command command,
                                      String label, String[] args) {
        if (!sender.isOp()) {
            sender.sendMessage("§cOnly operators can undo divine constructions.");
            return true;
        }
        String result = undoLastBuild();
        sender.sendMessage(result);
        return true;
    }

    // ─── Utility ────────────────────────────────────────────────────────────────

    private JsonObject locationToJson(Location loc) {
        JsonObject obj = new JsonObject();
        obj.addProperty("x", loc.getBlockX());
        obj.addProperty("y", loc.getBlockY());
        obj.addProperty("z", loc.getBlockZ());
        return obj;
    }

    private String dimensionId(World world) {
        return switch (world.getEnvironment()) {
            case NETHER -> "minecraft:the_nether";
            case THE_END -> "minecraft:the_end";
            default -> "minecraft:overworld";
        };
    }

    private void sendEvent(String eventType, JsonObject data) {
        data.addProperty("type", eventType);
        data.addProperty("timestamp", System.currentTimeMillis());
        String payload = gson.toJson(data);

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(BACKEND_URL + "/event"))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(payload))
                .timeout(Duration.ofSeconds(5))
                .build();

        httpClient.sendAsync(request, HttpResponse.BodyHandlers.ofString())
                .exceptionally(e -> null); // silently fail — god simply "blinked"
    }

    // ─── Event Handlers ─────────────────────────────────────────────────────────

    @EventHandler
    public void onChat(AsyncPlayerChatEvent event) {
        Player player = event.getPlayer();
        String message = event.getMessage();
        // Build full player snapshot on the main thread (needed for block/entity scanning),
        // then send the chat event with the snapshot embedded.
        // 1-tick delay is negligible — the player hasn't moved.
        Bukkit.getScheduler().runTask(this, () -> {
            JsonObject data = new JsonObject();
            data.addProperty("player", player.getName());
            data.addProperty("message", message);
            data.add("location", locationToJson(player.getLocation()));
            data.addProperty("dimension", dimensionId(player.getWorld()));
            data.add("playerSnapshot", buildPlayerSnapshot(player));
            sendEvent("chat", data);
        });
    }

    @EventHandler
    public void onPlayerJoin(PlayerJoinEvent event) {
        Player player = event.getPlayer();
        JsonObject data = new JsonObject();
        data.addProperty("player", player.getName());
        sendEvent("player_join", data);

        if (!player.hasPlayedBefore()) {
            JsonObject spawnData = new JsonObject();
            spawnData.addProperty("player", player.getName());
            spawnData.add("location", locationToJson(player.getLocation()));
            spawnData.addProperty("dimension", dimensionId(player.getWorld()));
            sendEvent("player_initial_spawn", spawnData);
        }
    }

    @EventHandler
    public void onPlayerQuit(PlayerQuitEvent event) {
        JsonObject data = new JsonObject();
        data.addProperty("player", event.getPlayer().getName());
        sendEvent("player_leave", data);
    }

    @EventHandler
    public void onEntityDeath(EntityDeathEvent event) {
        Entity entity = event.getEntity();
        boolean isPlayer = entity instanceof Player;

        JsonObject data = new JsonObject();
        data.addProperty("entity", entity.getType().getKey().toString());
        data.addProperty("entityName", isPlayer ? entity.getName()
                : entity.getType().getKey().toString());
        data.addProperty("isPlayer", isPlayer);
        if (isPlayer) {
            data.addProperty("playerName", entity.getName());
        } else {
            data.add("playerName", JsonNull.INSTANCE);
        }

        if (entity.getLastDamageCause() != null) {
            data.addProperty("cause",
                    entity.getLastDamageCause().getCause().name().toLowerCase());
            if (entity.getLastDamageCause() instanceof EntityDamageByEntityEvent dmgEvent) {
                data.addProperty("damagingEntity",
                        dmgEvent.getDamager().getType().getKey().toString());
            }
        }

        data.add("location", locationToJson(entity.getLocation()));
        data.addProperty("dimension", dimensionId(entity.getWorld()));
        data.addProperty("biome", entity.getLocation().getBlock().getBiome().getKey().toString().replace("minecraft:", ""));
        sendEvent("entity_die", data);
    }

    @EventHandler
    public void onBlockBreak(BlockBreakEvent event) {
        JsonObject data = new JsonObject();
        data.addProperty("player", event.getPlayer().getName());
        data.addProperty("block", event.getBlock().getType().getKey().toString());
        data.add("location", locationToJson(event.getBlock().getLocation()));
        data.addProperty("dimension", dimensionId(event.getPlayer().getWorld()));
        sendEvent("block_break", data);
    }

    @EventHandler
    public void onBlockPlace(BlockPlaceEvent event) {
        JsonObject data = new JsonObject();
        data.addProperty("player", event.getPlayer().getName());
        data.addProperty("block", event.getBlock().getType().getKey().toString());
        data.add("location", locationToJson(event.getBlock().getLocation()));
        data.addProperty("dimension", dimensionId(event.getPlayer().getWorld()));
        sendEvent("block_place", data);
    }

    @EventHandler
    public void onEntityDamage(EntityDamageByEntityEvent event) {
        Entity hurt = event.getEntity();
        Entity attacker = event.getDamager();
        boolean isPlayerHurt = hurt instanceof Player;
        boolean isPlayerAttacker = attacker instanceof Player;

        if (!isPlayerHurt && !isPlayerAttacker) return;

        JsonObject data = new JsonObject();
        data.addProperty("hurtEntity", hurt.getType().getKey().toString());
        data.addProperty("hurtEntityName",
                isPlayerHurt ? hurt.getName() : hurt.getType().getKey().toString());
        data.addProperty("damage", event.getDamage());
        data.addProperty("cause", event.getCause().name().toLowerCase());
        data.addProperty("attacker", attacker.getType().getKey().toString());
        if (isPlayerAttacker) {
            data.addProperty("attackerName", attacker.getName());
        } else {
            data.add("attackerName", JsonNull.INSTANCE);
        }
        data.add("location", locationToJson(hurt.getLocation()));
        data.addProperty("dimension", dimensionId(hurt.getWorld()));
        sendEvent("combat", data);
    }

    @EventHandler
    public void onWeatherChange(WeatherChangeEvent event) {
        JsonObject data = new JsonObject();
        data.addProperty("newWeather", event.toWeatherState() ? "rain" : "clear");
        data.addProperty("dimension", dimensionId(event.getWorld()));
        sendEvent("weather_change", data);
    }

    @EventHandler
    public void onThunderChange(ThunderChangeEvent event) {
        JsonObject data = new JsonObject();
        String weather = event.toThunderState() ? "thunder"
                : (event.getWorld().hasStorm() ? "rain" : "clear");
        data.addProperty("newWeather", weather);
        data.addProperty("dimension", dimensionId(event.getWorld()));
        sendEvent("weather_change", data);
    }

    // ─── Player Snapshot ────────────────────────────────────────────────────────

    /**
     * Build a full snapshot of a player's state: position, health, inventory,
     * nearby entities/blocks, what they're looking at, etc.
     *
     * MUST be called on the main server thread (accesses world state).
     * Used by both the periodic status beacon and inline with chat events
     * so that prayers always have accurate context.
     */
    private JsonObject buildPlayerSnapshot(Player p) {
        JsonObject ps = new JsonObject();
        ps.addProperty("name", p.getName());
        ps.add("location", locationToJson(p.getLocation()));
        ps.addProperty("dimension", dimensionId(p.getWorld()));
        ps.addProperty("health", p.getHealth());
        ps.addProperty("maxHealth", p.getMaxHealth());
        ps.addProperty("foodLevel", p.getFoodLevel());
        ps.addProperty("level", p.getLevel());
        ps.addProperty("gameMode", p.getGameMode().name().toLowerCase());

        // Facing direction — cardinal from yaw, pitch description
        float yaw = p.getLocation().getYaw() % 360;
        if (yaw < 0) yaw += 360;
        String[] cardinals = {"S", "SW", "W", "NW", "N", "NE", "E", "SE"};
        String facing = cardinals[Math.round(yaw / 45f) % 8];
        ps.addProperty("facing", facing);
        float pitch = p.getLocation().getPitch();
        String lookVertical = pitch < -45 ? "up" : pitch > 45 ? "down" : "ahead";
        ps.addProperty("lookingVertical", lookVertical);

        // Biome
        ps.addProperty("biome", p.getLocation().getBlock().getBiome().getKey().toString().replace("minecraft:", ""));

        // Armor
        JsonArray armor = new JsonArray();
        var equipment = p.getInventory();
        if (equipment.getHelmet() != null)
            armor.add(equipment.getHelmet().getType().getKey().toString());
        if (equipment.getChestplate() != null)
            armor.add(equipment.getChestplate().getType().getKey().toString());
        if (equipment.getLeggings() != null)
            armor.add(equipment.getLeggings().getType().getKey().toString());
        if (equipment.getBoots() != null)
            armor.add(equipment.getBoots().getType().getKey().toString());
        ps.add("armor", armor);

        // Main hand item
        var mainHand = equipment.getItemInMainHand();
        if (mainHand.getType() != org.bukkit.Material.AIR) {
            ps.addProperty("mainHand", mainHand.getType().getKey().toString());
        }

        // Full inventory — aggregate all items by type
        JsonObject inventory = new JsonObject();
        var inv = p.getInventory();
        for (var stack : inv.getContents()) {
            if (stack == null || stack.getType() == org.bukkit.Material.AIR) continue;
            String id = stack.getType().getKey().toString().replace("minecraft:", "");
            int existing = inventory.has(id) ? inventory.get(id).getAsInt() : 0;
            inventory.addProperty(id, existing + stack.getAmount());
        }
        ps.add("inventory", inventory);

        // Nearby entities within 32 blocks — aggregate by type
        JsonObject nearbyEntities = new JsonObject();
        // Close-range entities within 8 blocks — immediate surroundings
        JsonObject closeEntities = new JsonObject();
        for (Entity entity : p.getNearbyEntities(32, 32, 32)) {
            var type = entity.getType();
            if (type == org.bukkit.entity.EntityType.ITEM
                    || type == org.bukkit.entity.EntityType.EXPERIENCE_ORB
                    || type == org.bukkit.entity.EntityType.ARROW
                    || type == org.bukkit.entity.EntityType.MARKER) continue;
            String id = type.getKey().toString().replace("minecraft:", "");
            int existing = nearbyEntities.has(id) ? nearbyEntities.get(id).getAsInt() : 0;
            nearbyEntities.addProperty(id, existing + 1);
            // Also check if within close range
            if (entity.getLocation().distance(p.getLocation()) <= CLOSE_RANGE) {
                int closeExisting = closeEntities.has(id) ? closeEntities.get(id).getAsInt() : 0;
                closeEntities.addProperty(id, closeExisting + 1);
            }
        }
        ps.add("nearbyEntities", nearbyEntities);
        ps.add("closeEntities", closeEntities);

        // Notable blocks within 8 blocks
        JsonObject notableBlocks = new JsonObject();
        Location pLoc = p.getLocation();
        int px = pLoc.getBlockX(), py = pLoc.getBlockY(), pz = pLoc.getBlockZ();
        World world = p.getWorld();
        for (int dx = -BLOCK_SCAN_RANGE; dx <= BLOCK_SCAN_RANGE; dx++) {
            for (int dy = -BLOCK_SCAN_RANGE; dy <= BLOCK_SCAN_RANGE; dy++) {
                for (int dz = -BLOCK_SCAN_RANGE; dz <= BLOCK_SCAN_RANGE; dz++) {
                    var block = world.getBlockAt(px + dx, py + dy, pz + dz);
                    if (NOTABLE_BLOCKS.contains(block.getType())) {
                        String id = block.getType().getKey().toString().replace("minecraft:", "");
                        int existing = notableBlocks.has(id) ? notableBlocks.get(id).getAsInt() : 0;
                        notableBlocks.addProperty(id, existing + 1);
                    }
                }
            }
        }
        ps.add("notableBlocks", notableBlocks);

        // What the player is looking at (crosshair target)
        var targetBlock = p.getTargetBlockExact(5);
        if (targetBlock != null && targetBlock.getType() != org.bukkit.Material.AIR) {
            JsonObject lookingAt = new JsonObject();
            lookingAt.addProperty("block", targetBlock.getType().getKey().toString().replace("minecraft:", ""));
            lookingAt.add("blockLocation", locationToJson(targetBlock.getLocation()));
            ps.add("lookingAt", lookingAt);
        }
        var targetEntity = p.getTargetEntity(5);
        if (targetEntity != null) {
            if (!ps.has("lookingAt")) {
                ps.add("lookingAt", new JsonObject());
            }
            var lookingAt = ps.getAsJsonObject("lookingAt");
            String entityId = targetEntity.getType().getKey().toString().replace("minecraft:", "");
            if (targetEntity instanceof Player) {
                lookingAt.addProperty("entity", targetEntity.getName());
            } else {
                lookingAt.addProperty("entity", entityId);
            }
        }

        return ps;
    }

    // ─── Player Status Beacon ───────────────────────────────────────────────────

    private void sendPlayerStatus() {
        var players = Bukkit.getOnlinePlayers();
        if (players.isEmpty()) return;

        JsonArray statusArray = new JsonArray();
        for (Player p : players) {
            statusArray.add(buildPlayerSnapshot(p));
        }

        JsonObject data = new JsonObject();
        data.add("players", statusArray);
        sendEvent("player_status", data);
    }

    // ─── Command Polling ────────────────────────────────────────────────────────

    private void pollCommands() {
        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(BACKEND_URL + "/commands"))
                .GET()
                .timeout(Duration.ofSeconds(5))
                .build();

        httpClient.sendAsync(request, HttpResponse.BodyHandlers.ofString())
                .thenAccept(response -> {
                    if (response.statusCode() != 200) return;

                    JsonArray commands;
                    try {
                        commands = JsonParser.parseString(response.body()).getAsJsonArray();
                    } catch (Exception e) {
                        return;
                    }

                    if (commands.isEmpty()) return;

                    getLogger().info("Received " + commands.size() + " command(s) from backend");

                    // Must execute commands on the main thread
                    new BukkitRunnable() {
                        @Override
                        public void run() {
                            for (JsonElement elem : commands) {
                                executeCommand(elem.getAsJsonObject());
                            }
                        }
                    }.runTask(MinecraftGodPlugin.this);
                })
                .exceptionally(e -> null); // backend down — silently fail
    }

    private void executeCommand(JsonObject cmd) {
        // Check for special command types
        if (cmd.has("type")) {
            String type = cmd.get("type").getAsString();
            if ("build_schematic".equals(type)) {
                getLogger().info("Executing build_schematic: " + cmd);
                executeSchematicBuild(cmd);
                return;
            }
            if ("undo_last_build".equals(type)) {
                getLogger().info("Executing undo_last_build");
                String result = undoLastBuild();
                getLogger().info("Undo result: " + result);
                return;
            }
        }

        String command = cmd.get("command").getAsString();
        String targetPlayer = cmd.has("target_player") && !cmd.get("target_player").isJsonNull()
                ? cmd.get("target_player").getAsString() : null;

        try {
            if (targetPlayer != null) {
                Player player = Bukkit.getPlayerExact(targetPlayer);
                if (player != null && player.isOnline()) {
                    // Replace @s with player name
                    command = command.replace("@s", player.getName());
                    // Resolve relative coordinates to player's position
                    command = resolveRelativeCoords(command, player.getLocation());
                }
            }
            getLogger().info("Executing: " + command.substring(0, Math.min(command.length(), 120)));
            Bukkit.dispatchCommand(Bukkit.getConsoleSender(), command);
        } catch (Exception e) {
            getLogger().warning("Command failed: " + command + " — " + e.getMessage());
        }
    }

    // ─── Schematic Building ──────────────────────────────────────────────────────

    private void executeSchematicBuild(JsonObject cmd) {
        String blueprintId = cmd.get("blueprint_id").getAsString();
        int originX = cmd.get("x").getAsInt();
        int originY = cmd.get("y").getAsInt();
        int originZ = cmd.get("z").getAsInt();
        int rotation = cmd.has("rotation") ? cmd.get("rotation").getAsInt() : 0;

        // Validate blueprint ID (alphanumeric + hyphens only)
        if (!blueprintId.matches("^[a-z0-9-]+$")) {
            getLogger().warning("Blocked invalid blueprint ID: " + blueprintId);
            return;
        }

        File schemFile = new File(schematicsDir, blueprintId + ".schem");
        if (!schemFile.exists()) {
            getLogger().warning("Schematic not found: " + schemFile.getAbsolutePath());
            return;
        }

        // Load schematic on an async thread to avoid blocking the main thread
        Bukkit.getScheduler().runTaskAsynchronously(this, () -> {
            try {
                Schematic schem = SchematicLoader.load(schemFile.toPath());

                // Collect all block placements, sorted bottom-to-top
                List<BlockPlacement> placements = new ArrayList<>();
                schem.blocks().forEach(pair -> {
                    var pos = pair.left();
                    var block = pair.right();
                    String blockId = block.block();
                    if (blockId == null || blockId.equals("minecraft:air")) return;

                    // Build full block state string, rotating directional states if needed
                    var states = block.states();
                    Map<String, String> rotatedStates = new LinkedHashMap<>();
                    if (states != null && !states.isEmpty()) {
                        for (var entry : states.entrySet()) {
                            String key = entry.getKey();
                            String val = entry.getValue().toString();
                            if (rotation != 0) {
                                val = rotateBlockState(key, val, rotation);
                            }
                            rotatedStates.put(key, val);
                        }
                    }
                    StringBuilder stateStr = new StringBuilder(blockId);
                    if (!rotatedStates.isEmpty()) {
                        stateStr.append("[");
                        boolean first = true;
                        for (var entry : rotatedStates.entrySet()) {
                            if (!first) stateStr.append(",");
                            stateStr.append(entry.getKey()).append("=").append(entry.getValue());
                            first = false;
                        }
                        stateStr.append("]");
                    }

                    // Apply rotation to position
                    int rx = pos.x, rz = pos.z;
                    if (rotation == 90) { rx = -pos.z; rz = pos.x; }
                    else if (rotation == 180) { rx = -pos.x; rz = -pos.z; }
                    else if (rotation == 270) { rx = pos.z; rz = -pos.x; }

                    placements.add(new BlockPlacement(
                            originX + rx,
                            originY + pos.y,
                            originZ + rz,
                            stateStr.toString()
                    ));
                });

                // Sort bottom-to-top (by Y, then X, then Z)
                placements.sort(Comparator.comparingInt((BlockPlacement p) -> p.y)
                        .thenComparingInt(p -> p.x)
                        .thenComparingInt(p -> p.z));

                // Compute bounding box for terrain clearing
                int minX = Integer.MAX_VALUE, minY = Integer.MAX_VALUE, minZ = Integer.MAX_VALUE;
                int maxX = Integer.MIN_VALUE, maxY = Integer.MIN_VALUE, maxZ = Integer.MIN_VALUE;
                for (BlockPlacement bp : placements) {
                    minX = Math.min(minX, bp.x); maxX = Math.max(maxX, bp.x);
                    minY = Math.min(minY, bp.y); maxY = Math.max(maxY, bp.y);
                    minZ = Math.min(minZ, bp.z); maxZ = Math.max(maxZ, bp.z);
                }
                final int clearMinX = minX, clearMinY = minY, clearMinZ = minZ;
                final int clearMaxX = maxX, clearMaxY = maxY, clearMaxZ = maxZ;

                getLogger().info("Schematic " + blueprintId + ": " + placements.size()
                        + " blocks, placing progressively at " + originX + "," + originY + "," + originZ);

                // Start progressive placement on the main thread
                Bukkit.getScheduler().runTask(this, () -> {
                    World world = Bukkit.getWorlds().get(0); // overworld

                    // Snapshot original blocks in the bounding box for undo
                    List<BlockPlacement> originalBlocks = new ArrayList<>();
                    for (int y = clearMinY; y <= clearMaxY; y++) {
                        for (int x = clearMinX; x <= clearMaxX; x++) {
                            for (int z = clearMinZ; z <= clearMaxZ; z++) {
                                Block block = world.getBlockAt(x, y, z);
                                originalBlocks.add(new BlockPlacement(
                                        x, y, z, block.getBlockData().getAsString()));
                            }
                        }
                    }
                    // Also snapshot positions from the placement list that fall outside
                    // the bounding box (shouldn't happen, but be safe)
                    for (BlockPlacement bp : placements) {
                        if (bp.x < clearMinX || bp.x > clearMaxX ||
                            bp.y < clearMinY || bp.y > clearMaxY ||
                            bp.z < clearMinZ || bp.z > clearMaxZ) {
                            Block block = world.getBlockAt(bp.x, bp.y, bp.z);
                            originalBlocks.add(new BlockPlacement(
                                    bp.x, bp.y, bp.z, block.getBlockData().getAsString()));
                        }
                    }

                    // Push to undo history
                    synchronized (buildHistory) {
                        if (buildHistory.size() >= MAX_UNDO_HISTORY) {
                            buildHistory.removeLast();
                        }
                        buildHistory.push(new BuildSnapshot(
                                blueprintId, world, originalBlocks, System.currentTimeMillis()));
                    }
                    getLogger().info("Schematic " + blueprintId + ": saved undo snapshot ("
                            + originalBlocks.size() + " blocks)");

                    // Clear terrain in the bounding box, skipping protected blocks
                    int cleared = 0;
                    for (int y = clearMinY; y <= clearMaxY; y++) {
                        for (int x = clearMinX; x <= clearMaxX; x++) {
                            for (int z = clearMinZ; z <= clearMaxZ; z++) {
                                Block block = world.getBlockAt(x, y, z);
                                if (!block.getType().isAir() && !isProtectedBlock(block.getType())) {
                                    block.setType(Material.AIR, false);
                                    cleared++;
                                }
                            }
                        }
                    }
                    if (cleared > 0) {
                        getLogger().info("Schematic " + blueprintId + ": cleared " + cleared
                                + " terrain blocks from build area");
                    }

                    // Lightning strike at build start
                    Bukkit.dispatchCommand(Bukkit.getConsoleSender(),
                            "summon minecraft:lightning_bolt " + originX + " " + originY + " " + originZ);

                    // Start the progressive placer
                    new SchematicPlacer(world, placements, blueprintId, originX, originY, originZ)
                            .runTaskTimer(MinecraftGodPlugin.this, 5, 1); // 5 tick delay, then every tick
                });

            } catch (Exception e) {
                getLogger().severe("Failed to load schematic " + blueprintId + ": " + e.getMessage());
                e.printStackTrace();
            }
        });
    }

    /** A single block to be placed as part of a schematic build. */
    private record BlockPlacement(int x, int y, int z, String blockState) {}

    /** Places schematic blocks progressively, bottom-to-top, for dramatic effect. */
    private class SchematicPlacer extends BukkitRunnable {
        private final World world;
        private final Queue<BlockPlacement> queue;
        private final List<BlockPlacement> allPlacements;
        private final String blueprintId;
        private final int originX, originY, originZ;
        private final int blocksPerTick;
        private int totalPlaced = 0;
        private final int totalBlocks;

        SchematicPlacer(World world, List<BlockPlacement> placements, String blueprintId,
                        int originX, int originY, int originZ) {
            this.world = world;
            this.queue = new ConcurrentLinkedQueue<>(placements);
            this.allPlacements = placements;
            this.blueprintId = blueprintId;
            this.originX = originX;
            this.originY = originY;
            this.originZ = originZ;
            this.totalBlocks = placements.size();
            // Scale blocks per tick based on size: small builds faster, large builds slower
            // Aim for ~5-15 seconds total build time
            this.blocksPerTick = Math.max(10, Math.min(200, totalBlocks / 100));
        }

        @Override
        public void run() {
            int placed = 0;
            while (!queue.isEmpty() && placed < blocksPerTick) {
                BlockPlacement bp = queue.poll();
                if (bp == null) break;
                try {
                    Block block = world.getBlockAt(bp.x, bp.y, bp.z);
                    // Don't overwrite player beds, chests, etc.
                    if (isProtectedBlock(block.getType())) {
                        totalPlaced++;
                        continue;
                    }
                    BlockData data = Bukkit.createBlockData(bp.blockState);
                    block.setBlockData(data, false); // skip physics for performance
                    placed++;
                    totalPlaced++;
                } catch (Exception e) {
                    // Skip blocks with invalid block states (e.g. removed in newer versions)
                    totalPlaced++;
                }
            }

            // Occasional particle effects during construction
            if (totalPlaced % 100 == 0 && totalPlaced > 0) {
                world.spawnParticle(Particle.CLOUD,
                        originX + 0.5, originY + (totalPlaced / (double) totalBlocks) * 20 + 0.5, originZ + 0.5,
                        15, 3, 1, 3, 0.01);
            }

            if (queue.isEmpty()) {
                this.cancel();
                getLogger().info("Schematic " + blueprintId + " complete: " + totalPlaced + " blocks placed");

                // Tick all placed blocks to activate redstone, water flow, etc.
                // setBlockData(data, true) only notifies neighbors — tick() actually
                // schedules the block itself (repeaters start cycling, observers fire, etc.)
                int ticked = 0;
                for (BlockPlacement bp : allPlacements) {
                    try {
                        Block block = world.getBlockAt(bp.x, bp.y, bp.z);
                        if (!block.getType().isAir()) {
                            block.tick();
                            ticked++;
                        }
                    } catch (Exception ignored) {}
                }
                getLogger().info("Schematic " + blueprintId + ": ticked " + ticked + " blocks");

                // Completion effects
                world.spawnParticle(Particle.TOTEM_OF_UNDYING,
                        originX + 0.5, originY + 5, originZ + 0.5,
                        50, 3, 3, 3, 0.1);
                Bukkit.dispatchCommand(Bukkit.getConsoleSender(),
                        "playsound minecraft:ui.toast.challenge_complete master @a "
                                + originX + " " + originY + " " + originZ + " 2 1");
            }
        }
    }

    // ─── Build Undo ─────────────────────────────────────────────────────────────

    /**
     * Undo the most recent schematic build by restoring original block states.
     * Returns a human-readable result message.
     */
    private String undoLastBuild() {
        BuildSnapshot snapshot;
        synchronized (buildHistory) {
            snapshot = buildHistory.poll();
        }
        if (snapshot == null) {
            return "§eNo builds to undo.";
        }

        int restored = 0;
        for (BlockPlacement bp : snapshot.originalBlocks()) {
            try {
                Block block = snapshot.world().getBlockAt(bp.x, bp.y, bp.z);
                BlockData data = Bukkit.createBlockData(bp.blockState);
                block.setBlockData(data, false);
                restored++;
            } catch (Exception e) {
                // Skip blocks with invalid states (e.g. mod blocks no longer loaded)
            }
        }

        getLogger().info("Undo: restored " + restored + " blocks (was: " + snapshot.blueprintId() + ")");

        // Visual feedback
        Bukkit.dispatchCommand(Bukkit.getConsoleSender(),
                "playsound minecraft:entity.evoker.prepare_wololo master @a");

        return "§aUndid §f" + snapshot.blueprintId() + "§a (" + restored + " blocks restored)";
    }

    // ─── Protected Blocks ──────────────────────────────────────────────────────────

    /** Blocks that should never be overwritten by schematic terrain clearing. */
    private static final Set<Material> PROTECTED_BLOCKS = Set.of(
            // Player spawn points
            Material.WHITE_BED, Material.ORANGE_BED, Material.MAGENTA_BED, Material.LIGHT_BLUE_BED,
            Material.YELLOW_BED, Material.LIME_BED, Material.PINK_BED, Material.GRAY_BED,
            Material.LIGHT_GRAY_BED, Material.CYAN_BED, Material.PURPLE_BED, Material.BLUE_BED,
            Material.BROWN_BED, Material.GREEN_BED, Material.RED_BED, Material.BLACK_BED,
            Material.RESPAWN_ANCHOR,
            // Storage
            Material.CHEST, Material.TRAPPED_CHEST, Material.BARREL, Material.ENDER_CHEST,
            Material.SHULKER_BOX, Material.WHITE_SHULKER_BOX, Material.ORANGE_SHULKER_BOX,
            Material.MAGENTA_SHULKER_BOX, Material.LIGHT_BLUE_SHULKER_BOX, Material.YELLOW_SHULKER_BOX,
            Material.LIME_SHULKER_BOX, Material.PINK_SHULKER_BOX, Material.GRAY_SHULKER_BOX,
            Material.LIGHT_GRAY_SHULKER_BOX, Material.CYAN_SHULKER_BOX, Material.PURPLE_SHULKER_BOX,
            Material.BLUE_SHULKER_BOX, Material.BROWN_SHULKER_BOX, Material.GREEN_SHULKER_BOX,
            Material.RED_SHULKER_BOX, Material.BLACK_SHULKER_BOX,
            // Other valuables
            Material.BEACON, Material.ENCHANTING_TABLE, Material.ANVIL,
            Material.CHIPPED_ANVIL, Material.DAMAGED_ANVIL
    );

    private static boolean isProtectedBlock(Material mat) {
        return PROTECTED_BLOCKS.contains(mat);
    }

    // ─── Block State Rotation ─────────────────────────────────────────────────────

    private static final Map<String, String> FACING_CW = Map.of(
            "north", "east", "east", "south", "south", "west", "west", "north");

    /**
     * Rotate a single block state value for the given rotation (90, 180, 270).
     * Handles: facing (N/E/S/W), axis (x/z swap), rotation (0-15 sign posts),
     * and shape (inner/outer stair corners).
     */
    private static String rotateBlockState(String key, String value, int rotation) {
        int steps = rotation / 90; // 1, 2, or 3 quarter-turns CW
        switch (key) {
            case "facing": {
                String v = value.toLowerCase();
                // Only rotate horizontal facings
                if (v.equals("up") || v.equals("down")) return value;
                for (int i = 0; i < steps; i++) {
                    v = FACING_CW.getOrDefault(v, v);
                }
                return v;
            }
            case "axis": {
                // x ↔ z on 90/270, both flip on 180 (back to same)
                if (steps == 1 || steps == 3) {
                    if (value.equals("x")) return "z";
                    if (value.equals("z")) return "x";
                }
                return value;
            }
            case "rotation": {
                // Sign/banner rotation: 0-15, each step = 4 increments CW
                try {
                    int r = Integer.parseInt(value);
                    return String.valueOf((r + steps * 4) % 16);
                } catch (NumberFormatException e) {
                    return value;
                }
            }
            default:
                return value;
        }
    }

    /**
     * Replace tilde-relative coordinates (~, ~5, ~-3) with absolute positions
     * based on a player's location. Handles groups of 3 consecutive tilde tokens.
     */
    private String resolveRelativeCoords(String command, Location loc) {
        String[] tokens = command.split(" ");
        for (int i = 0; i < tokens.length - 2; i++) {
            if (tokens[i].startsWith("~") && tokens[i + 1].startsWith("~") && tokens[i + 2].startsWith("~")) {
                tokens[i] = resolveTilde(tokens[i], loc.getBlockX());
                tokens[i + 1] = resolveTilde(tokens[i + 1], loc.getBlockY());
                tokens[i + 2] = resolveTilde(tokens[i + 2], loc.getBlockZ());
                break; // only replace the first group
            }
        }
        return String.join(" ", tokens);
    }

    private String resolveTilde(String token, int base) {
        if (token.equals("~")) return String.valueOf(base);
        try {
            int offset = Integer.parseInt(token.substring(1));
            return String.valueOf(base + offset);
        } catch (NumberFormatException e) {
            return String.valueOf(base);
        }
    }
}
