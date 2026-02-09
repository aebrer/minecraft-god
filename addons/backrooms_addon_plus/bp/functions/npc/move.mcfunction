tp @s ~ ~-0.1 ~ true
tp @s ~ ~-0.2 ~ true
tp @s[scores={npc_tick=50..}] ^ ^ ^0.1 true
tp @s[scores={npc_tick=25}] ~~~~90
execute if entity @e[family=backroom,r=5] run tp @s ^ ^ ^-0.2 true
execute if entity @e[family=backroom,r=5] run tp @s ~ ~ ~ facing @e[family=backroom,c=1]
execute if entity @e[family=backroom,r=0.8] run kill @s
tp @s ~ ~0.1 ~