structure load level36 0 120 0
setblock ~ ~ ~ air
tp @a 35.17 122.00 26.57
function hh_smoke
effect @a slowness 3 99 true
scoreboard players set @a level 38
tag @a remove smoke
kill @e[type=hh:entity_4]
title @a subtitle §2Level§7{§137§7}
title @a title The Backrooms
kill @e[type=hh:smiler]
title @a times 1 60 0