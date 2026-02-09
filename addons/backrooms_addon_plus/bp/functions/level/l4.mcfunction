structure load level4 0 120 0
setblock ~ ~ ~ air
tp @a 27 121 25
function hh_smoke
effect @a slowness 3 99 true
scoreboard players set @a level 37
tag @a remove smoke
kill @e[type=hh:smiler]
summon hh:entity_4 28.62 121.00 41.29
title @a subtitle §2Level§7{§14§7}
title @a title The Backrooms
kill @e[type=hh:entity_3]
kill @e[type=hh:steve_skin]
title @a times 1 60 0