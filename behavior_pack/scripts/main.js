/**
 * Minecraft God — Behavior Pack Script
 *
 * Listens to game events and forwards them to the Python backend via HTTP.
 * Polls for commands from the backend and executes them in-game.
 */

import { world, system } from "@minecraft/server";
import {
    http,
    HttpRequest,
    HttpRequestMethod,
    HttpHeader,
} from "@minecraft/server-net";

const BACKEND_URL = "http://localhost:8000";

// ─── Utility ────────────────────────────────────────────────────────────────

function vecToObj(vec) {
    return {
        x: Math.floor(vec.x),
        y: Math.floor(vec.y),
        z: Math.floor(vec.z),
    };
}

function sendEvent(eventType, data) {
    const payload = JSON.stringify({
        type: eventType,
        ...data,
        timestamp: Date.now(),
    });

    const request = new HttpRequest(`${BACKEND_URL}/event`);
    request.method = HttpRequestMethod.Post;
    request.body = payload;
    request.headers = [new HttpHeader("Content-Type", "application/json")];

    http.request(request).catch((err) => {
        // Silently fail if backend is down — god simply "blinked"
    });
}

// ─── Event Subscriptions ────────────────────────────────────────────────────

// Chat — highest priority, primary interaction with god
world.afterEvents.chatSend.subscribe((event) => {
    sendEvent("chat", {
        player: event.sender.name,
        message: event.message,
        location: vecToObj(event.sender.location),
        dimension: event.sender.dimension.id,
    });
});

// Player join
world.afterEvents.playerJoin.subscribe((event) => {
    sendEvent("player_join", {
        player: event.playerName,
    });
});

// Player leave
world.afterEvents.playerLeave.subscribe((event) => {
    sendEvent("player_leave", {
        player: event.playerName,
    });
});

// Player spawn (initial = first time joining this world)
world.afterEvents.playerSpawn.subscribe((event) => {
    if (event.initialSpawn) {
        sendEvent("player_initial_spawn", {
            player: event.player.name,
            location: vecToObj(event.player.location),
            dimension: event.player.dimension.id,
        });
    }
});

// Entity death — player deaths and notable mob kills
world.afterEvents.entityDie.subscribe((event) => {
    const entity = event.deadEntity;
    const source = event.damageSource;

    sendEvent("entity_die", {
        entity: entity.typeId,
        entityName: entity.nameTag || entity.typeId,
        isPlayer: entity.typeId === "minecraft:player",
        playerName:
            entity.typeId === "minecraft:player" ? entity.name : null,
        cause: source.cause,
        damagingEntity: source.damagingEntity
            ? source.damagingEntity.typeId
            : null,
        location: vecToObj(entity.location),
        dimension: entity.dimension.id,
    });
});

// Block break — important for Deep God triggers (Y level, ore type)
world.afterEvents.playerBreakBlock.subscribe((event) => {
    sendEvent("block_break", {
        player: event.player.name,
        block: event.brokenBlockPermutation.type.id,
        location: vecToObj(event.block.location),
        dimension: event.player.dimension.id,
    });
});

// Block place
world.afterEvents.playerPlaceBlock.subscribe((event) => {
    sendEvent("block_place", {
        player: event.player.name,
        block: event.block.typeId,
        location: vecToObj(event.block.location),
        dimension: event.player.dimension.id,
    });
});

// Combat — only track when players are involved
world.afterEvents.entityHurt.subscribe((event) => {
    const hurtEntity = event.hurtEntity;
    const source = event.damageSource;
    const isPlayerHurt = hurtEntity.typeId === "minecraft:player";
    const isPlayerAttacker =
        source.damagingEntity &&
        source.damagingEntity.typeId === "minecraft:player";

    if (isPlayerHurt || isPlayerAttacker) {
        sendEvent("combat", {
            hurtEntity: hurtEntity.typeId,
            hurtEntityName:
                hurtEntity.typeId === "minecraft:player"
                    ? hurtEntity.name
                    : hurtEntity.typeId,
            damage: event.damage,
            cause: source.cause,
            attacker: source.damagingEntity
                ? source.damagingEntity.typeId
                : null,
            attackerName:
                source.damagingEntity &&
                source.damagingEntity.typeId === "minecraft:player"
                    ? source.damagingEntity.name
                    : null,
            location: vecToObj(hurtEntity.location),
            dimension: hurtEntity.dimension.id,
        });
    }
});

// Weather changes
world.afterEvents.weatherChange.subscribe((event) => {
    sendEvent("weather_change", {
        lightning: event.lightning,
        raining: event.raining,
        dimension: event.dimension.id,
    });
});

// ─── Player Status Beacon ───────────────────────────────────────────────────
// Send player positions, health, and level every 30 seconds (600 ticks)

system.runInterval(() => {
    const players = world.getAllPlayers();
    if (players.length === 0) return;

    const status = players.map((p) => {
        const health = p.getComponent("minecraft:health");
        return {
            name: p.name,
            location: vecToObj(p.location),
            dimension: p.dimension.id,
            health: health ? health.currentValue : 0,
            level: p.level,
        };
    });

    sendEvent("player_status", { players: status });
}, 600);

// ─── Command Polling ────────────────────────────────────────────────────────
// Every 5 seconds (100 ticks), poll the backend for commands to execute

system.runInterval(() => {
    const request = new HttpRequest(`${BACKEND_URL}/commands`);
    request.method = HttpRequestMethod.Get;

    http.request(request)
        .then((response) => {
            if (response.status !== 200) return;

            let commands;
            try {
                commands = JSON.parse(response.body);
            } catch {
                return;
            }

            if (!Array.isArray(commands) || commands.length === 0) return;

            for (const cmd of commands) {
                try {
                    if (cmd.target_player) {
                        // Execute relative to a specific player
                        const players = world
                            .getAllPlayers()
                            .filter((p) => p.name === cmd.target_player);
                        if (players.length > 0) {
                            players[0].runCommand(cmd.command);
                        }
                    } else {
                        // Execute in overworld dimension
                        world
                            .getDimension("overworld")
                            .runCommand(cmd.command);
                    }
                } catch (err) {
                    // Command failed — log but don't crash
                    console.warn(
                        `[god] Command failed: ${cmd.command} - ${err}`
                    );
                }
            }
        })
        .catch(() => {
            // Backend is down — silently fail
        });
}, 100);
