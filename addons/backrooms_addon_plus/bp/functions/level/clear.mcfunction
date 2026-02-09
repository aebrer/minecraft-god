effect @a night_vision 0




structure load clear 0 120 0
setblock ~ ~ ~ air
tp @a 8.71 117.20 12.46
scoreboard players set @a level 0
effect @a resistance 20 99 true
tag @a remove smoke
kill @e[family=backroom]

setblock 21 121 41 air
setblock 18 121 41 air
setblock 18 121 37 air
setblock 19 121 36 air
title @a reset
fill 2.52 47.00 19.70 18.49 61.50 2.92 dirt
fill 2.52 62.00 19.70 18.49 62.50 2.92 grass_block
fill 25.57 47.00 -8.45 -8.11 61.20 33.71 dirt
fill 25.57 62.00 -8.45 -8.11 62.20 33.71 grass_block
kill @e[type=item]
effect @a night_vision 20 99 true