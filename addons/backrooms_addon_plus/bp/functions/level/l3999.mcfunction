structure load level3999 0 120 -7
setblock ~ ~ ~ air
tp @a 35.24 121.00 32.54
tp @e[type=hh:steve] 35.24 121.00 32.54
function hh_smoke
effect @a slowness 1 99 true
scoreboard players set @a level 0
tag @a remove smoke
title @a subtitle §2Level§7{§13999§7}
title @a title The Backrooms
title @a times 1 60 0