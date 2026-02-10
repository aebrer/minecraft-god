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
            ps.addProperty("level", p.getLevel());
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
