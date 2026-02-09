scoreboard objectives add ticktime dummy
execute as @a at @s run execute if entity @e[type=hh:wormlings,r=2] run tag @s add ticktime
execute as @a[tag=ticktime] at @s run function tick/morph