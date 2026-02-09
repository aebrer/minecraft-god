execute as @e[type=hh:steve] at @s run function npc/move
execute as @e[type=hh:steve] at @s run scoreboard players add @s npc_tick 1
execute as @e[type=hh:steve] at @s run scoreboard players set @s[scores={npc_tick=200..}] npc_tick 0
scoreboard objectives add npc_tick dummy