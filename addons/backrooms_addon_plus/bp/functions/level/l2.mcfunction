structure load level2 0 120 0
setblock ~ ~ ~ air
tp @a 41 121 35
function hh_smoke
effect @a slowness 3 99 true
scoreboard players set @a level 3
tag @a add smoke
title @a subtitle §2Level§7{§12§7}
title @a title The Backrooms
title @a times 1 60 0
kill @e[type=hh:smiler]