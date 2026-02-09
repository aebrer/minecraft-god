structure load level38 0 120 0
setblock ~ ~ ~ air
tp @a 25.51 122.00 24.91
function hh_smoke
effect @a slowness 3 99 true
scoreboard players set @a level 922337
tag @a remove smoke
kill @e[type=hh:entity_4]
title @a subtitle §2Level§7{§138§7}
title @a title The Backrooms
kill @e[type=hh:entity_5]
title @a times 1 60 0
summon hh:entity_5 23.42 122.00 13.32