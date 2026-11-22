<?xml version="1.0" encoding="UTF-8"?>
<tileset version="1.10" tiledversion="1.10.2" name="terrain" tilewidth="32" tileheight="32" spacing="1" margin="1" tilecount="48" columns="8" class="GroundTiles">
 <properties>
  <property name="kind" value="base"/>
  <property name="solid" type="bool" value="false"/>
 </properties>
 <image source="../../images/tmw_desert_spacing.png" width="265" height="199"/>
 <tile id="5">
  <properties>
   <property name="damage" type="int" value="3"/>
   <property name="note" value="spiky"/>
  </properties>
  <animation>
   <frame tileid="6" duration="100"/>
   <frame tileid="7" duration="150"/>
  </animation>
 </tile>
 <tile id="12" class="Special">
  <properties>
   <property name="glow" type="color" value="#80ff8800"/>
  </properties>
 </tile>
</tileset>
