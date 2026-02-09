execute as @e[type=hh:steve_skin] at @s run tp @s ~ ~ ~ facing @p
execute as @e[type=hh:steve_skin] at @s run tp @s ~ ~-0.1 ~ true
execute as @e[type=hh:steve_skin] at @s run tp @s ~ ~-0.2 ~ true
execute as @e[type=hh:steve_skin] at @s run execute if entity @a[r=2] run function entity/SkinStealer
execute as @e[type=hh:entity_5] at @s run tp @s ^ ^ ^0.3 facing @e[type=armor_stand,name=entity_5_facing,c=1]
execute as @e[type=armor_stand,name=entity_5_facing] at @s run execute if entity @e[type=hh:entity_5,r=0.2] run kill @s
effect @e[type=armor_stand,name=entity_5_facing] invisibility infinite 99 true
effect @e[type=hh:entity_5] speed infinite 1 true
effect @e[type=hh:smiler,name=l1] slowness infinite 1 true