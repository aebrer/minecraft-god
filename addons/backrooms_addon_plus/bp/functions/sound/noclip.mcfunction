scoreboard players add @a noclip 1
execute as @a[scores={noclip=62..}] at @s run scoreboard objectives remove noclip
execute as @a[scores={noclip=..60}] at @s run replaceitem entity @s slot.armor.head 0 carved_pumpkin
execute as @a[scores={noclip=..60}] at @s run execute as @e[family=entity] at @s run tp @s ~ ~ ~
execute as @a[scores={noclip=61}] at @s run replaceitem entity @s slot.armor.head 0 air
playsound dig.sand @a[scores={noclip=..60}]
stopsound @a[scores={noclip=61}]
