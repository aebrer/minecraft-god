execute as @e at @s run execute if block ~ ~ ~ hh:damage_block run damage @s 3
execute as @e at @s run execute if block ~ ~ ~ hh:bolt_wire run damage @s 3
execute as @a at @s run function bedrock_fill
fill 64 119 -1 -1 119 64 bedrock
fill 64 150 -1 -1 150 64 bedrock
fill -0.40 120.00 64.32 64.30 150.26 64.30 bedrock
fill -0.40 120.00 -1 64.30 150.26 -1 bedrock
fill 64.30 150.00 -0.30 64.30 120.00 63.70 bedrock 
fill -0.30 120.00 0.30 -0.30 150.00 64.30 bedrock
execute as @e[type=armor_stand,name=door_hh_key,tag=!destroy] at @s run setblock ~ ~ ~ barrier
execute as @e[type=armor_stand,name=door_hh_key,tag=!destroy] at @s run setblock ~ ~1 ~ barrier
execute as @e[type=armor_stand,name=door_hh_key,tag=destroy] at @s run setblock ~ ~ ~ air
execute as @e[type=armor_stand,name=door_hh_key,tag=destroy] at @s run setblock ~ ~1 ~ air
execute as @e[type=armor_stand,name=door_hh_key,tag=destroy] at @s run execute if block ~ ~ ~ air run function door_destroy
execute as @e[type=armor_stand,name=door_hh_key] at @s run execute if entity @a[tag=key,r=1] run tag @s add destroy
execute as @e[type=armor_stand,name=door_hh_key] at @s run effect @s invisibility infinite 99 true