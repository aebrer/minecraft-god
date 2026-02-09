setblock ~ ~ ~ air
execute if block ~ ~ ~1 hh:entity_spawn_block2 run setblock ~ ~ ~1 air
execute if block ~ ~ ~-1 hh:entity_spawn_block2 run setblock ~ ~ ~-1 air
summon hh:entity_5 ~-3 ~ ~5
summon armor_stand entity_5_facing ~-3 ~ ~