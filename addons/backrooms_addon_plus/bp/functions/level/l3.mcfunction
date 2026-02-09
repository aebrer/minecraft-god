structure load level3 0 120 0
setblock ~ ~ ~ air
tp @a 27 121 25
function hh_smoke
effect @a slowness 3 99 true
scoreboard players set @a level 4
tag @a remove smoke
title @a subtitle §2Level§7{§13§7}
title @a title The Backrooms
kill @e[type=hh:smiler]
summon hh:entity_3 18.29 121.00 25.53
summon hh:steve_skin 11.28 121.00 31.28
title @a times 1 60 0