scoreboard players add @s skin_walker 1
playanimation @s[scores={skin_walker=1}] animation.humanoids.skin_walker
execute as @s[scores={skin_walker=..50}] at @s run tp @s ~ ~ ~ ~ 0
playanimation @s[scores={skin_walker=50..}] animation.humanoids.skinwalker
inputpermission set @s[scores={skin_walker=1}] sneak disabled
execute as @s at @s run tp @e[type=hh:skin_walker,r=1] ~ ~ ~ facing ^ ^ ^10
execute as @s[scores={skin_walker=50}] at @s run summon hh:skin_walker ~ ~ ~ facing ^ ^ ^10
execute as @s[scores={skin_walker=50..}] at @s run tp @s ~ ~ ~ facing @e[family=!backroom,c=1,rm=1]
execute as @s[scores={skin_walker=50..}] at @s run tp @s ^ ^ ^0.05 true
execute if entity @e[family=!backroom,rm=1,r=2] run function event/clear

effect @s regeneration 1 99 true
effect @s saturation 1 99 true
effect @s resistance 1 99 true
effect @s instant_health 1 99 true
function event/clear2
camera @s fade time 0 1 0 color 0 0 0