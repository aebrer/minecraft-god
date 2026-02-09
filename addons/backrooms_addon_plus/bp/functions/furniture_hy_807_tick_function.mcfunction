gamerule sendcommandfeedback false
gamerule commandblockoutput false
effect @a[tag=smoke] blindness 3 99 true
function block/hh_block
function clip/clip_tick
scoreboard objectives add level_sub dummy
scoreboard players add @a level_sub 1
scoreboard players set @a[scores={level_sub=5..}] level_sub 3
scoreboard players set @a[scores={level_sub=1}] level 0
execute as @a[scores={level_sub=1}] at @s run function backrooms_structure
execute as @a[tag=edit] at @s run function edit_effect
function sound/noclip
execute as @e[type=hh:smiler] at @s run fill ~-3 ~ ~-3 ~3 ~4 ~3 air replace bed
execute as @e[type=hh:smiler] at @s run fill ~-3 ~ ~-3 ~3 ~4 ~3 air replace hh:oak_tables
function entity/hh_entity
function item/item_tick
function npc/npc_tick
function skin_walker/player_tick
function animation/animation_hh
function tick/skin_tick