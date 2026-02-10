package ca.entropist.minecraftgod;

import com.google.gson.*;
import org.bukkit.Bukkit;
import org.bukkit.Location;
import org.bukkit.World;
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

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.EnumSet;
import java.util.Set;

/**
 * Minecraft God — Paper Plugin
 *
 * Listens to game events and forwards them to the Python backend via HTTP.
 * Polls for commands from the backend and executes them in-game.
 *
 * Port of the Bedrock behavior pack (scripts/main.js) to Paper/Java.
 */
@SuppressWarnings("deprecation") // AsyncPlayerChatEvent — still functional, simpler than Adventure chat API
public class MinecraftGodPlugin extends JavaPlugin implements Listener {

    private static final String BACKEND_URL = "http://localhost:8000";
    private static final int CLOSE_RANGE = 8;
    private static final int BLOCK_SCAN_RANGE = 8;

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

        getLogger().info("The gods are watching.");
    }

    @Override
    public void onDisable() {
        getLogger().info("The gods have departed.");
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
        JsonObject data = new JsonObject();
        data.addProperty("player", event.getPlayer().getName());
        data.addProperty("message", event.getMessage());
        data.add("location", locationToJson(event.getPlayer().getLocation()));
        data.addProperty("dimension", dimensionId(event.getPlayer().getWorld()));
        sendEvent("chat", data);
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

    // ─── Player Status Beacon ───────────────────────────────────────────────────

    private void sendPlayerStatus() {
        var players = Bukkit.getOnlinePlayers();
        if (players.isEmpty()) return;

        JsonArray statusArray = new JsonArray();
        for (Player p : players) {
            JsonObject ps = new JsonObject();
            ps.addProperty("name", p.getName());
            ps.add("location", locationToJson(p.getLocation()));
            ps.addProperty("dimension", dimensionId(p.getWorld()));
            ps.addProperty("health", p.getHealth());
            ps.addProperty("maxHealth", p.getMaxHealth());
            ps.addProperty("foodLevel", p.getFoodLevel());
            ps.addProperty("level", p.getLevel());
            ps.addProperty("gameMode", p.getGameMode().name().toLowerCase());

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

            statusArray.add(ps);
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
            Bukkit.dispatchCommand(Bukkit.getConsoleSender(), command);
        } catch (Exception e) {
            getLogger().warning("Command failed: " + command + " — " + e.getMessage());
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
