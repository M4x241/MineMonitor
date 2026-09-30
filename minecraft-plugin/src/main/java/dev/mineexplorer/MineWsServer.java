package dev.mineexplorer;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.bukkit.Bukkit;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.World;
import org.bukkit.block.Block;
import org.bukkit.util.RayTraceResult;
import org.bukkit.util.Vector;
import org.java_websocket.WebSocket;
import org.java_websocket.handshake.ClientHandshake;
import org.java_websocket.server.WebSocketServer;

import java.net.InetSocketAddress;
import java.util.ArrayList;
import java.util.List;

/**
 * Servidor WebSocket que expone el simulador a Python:
 * raycast (LiDAR), move (interpolación), teleport, position, blocks,
 * ground_truth (etiquetas de riesgo) y status.
 * Todo el trabajo con el mundo se ejecuta en el hilo principal del servidor.
 */
public class MineWsServer extends WebSocketServer {

    private final MineExplorerPlugin plugin;
    private final ExplorerMob explorer;
    private final World world;
    private final MineGenerator generator;
    private final Gson gson = new Gson();
    private final long startMillis;

    public MineWsServer(MineExplorerPlugin plugin, int port, ExplorerMob explorer,
                        World world, MineGenerator generator) {
        super(new InetSocketAddress(port));
        this.plugin = plugin;
        this.explorer = explorer;
        this.world = world;
        this.generator = generator;
        this.startMillis = System.currentTimeMillis();
        setReuseAddr(true);
    }

    @Override
    public void onOpen(WebSocket conn, ClientHandshake handshake) {
        plugin.getLogger().info("Cliente conectado: " + conn.getRemoteSocketAddress());
    }

    @Override
    public void onClose(WebSocket conn, int code, String reason, boolean remote) {
        plugin.getLogger().info("Cliente desconectado: " + conn.getRemoteSocketAddress());
    }

    @Override
    public void onMessage(WebSocket conn, String message) {
        // El acceso al mundo debe ocurrir en el hilo principal.
        Bukkit.getScheduler().runTask(plugin, () -> handle(conn, message));
    }

    @Override
    public void onError(WebSocket conn, Exception ex) {
        plugin.getLogger().warning("Error WS: " + ex.getMessage());
    }

    @Override
    public void onStart() {
        plugin.getLogger().info("Servidor WebSocket escuchando en el puerto " + getPort());
    }

    private void handle(WebSocket conn, String message) {
        JsonObject req;
        try {
            req = JsonParser.parseString(message).getAsJsonObject();
        } catch (Exception ex) {
            replyError(conn, null, "JSON inválido: " + ex.getMessage());
            return;
        }
        String id = req.has("id") ? req.get("id").getAsString() : null;
        String type = req.has("type") ? req.get("type").getAsString() : "";
        try {
            switch (type) {
                case "status" -> reply(conn, id, buildStatus());
                case "position" -> reply(conn, id, buildPosition());
                case "regen" -> reply(conn, id, handleRegen(req));
                case "move" -> reply(conn, id, handleMove(req));
                case "teleport" -> reply(conn, id, handleTeleport(req));
                case "raycast" -> reply(conn, id, handleRaycast(req));
                case "blocks" -> reply(conn, id, handleBlocks(req, false));
                case "ground_truth" -> reply(conn, id, handleBlocks(req, true));
                default -> replyError(conn, id, "Tipo de mensaje desconocido: " + type);
            }
        } catch (Exception ex) {
            plugin.getLogger().warning("Error manejando '" + type + "': " + ex);
            replyError(conn, id, ex.getMessage());
        }
    }

    // ------------------------------------------------------------------ regen

    private JsonObject handleRegen(JsonObject req) {
        String newSeed = req.has("seed") ? req.get("seed").getAsString() : plugin.seed();
        plugin.regenerate(newSeed);
        plugin.getLogger().info("WS: mina regenerada con seed=" + newSeed);
        return buildStatus();
    }

    // ------------------------------------------------------------------ status

    private JsonObject buildStatus() {
        Location mob = explorer.getLocation();
        Location spawn = world.getSpawnLocation();
        Location mineSpawn = explorer.getOrigin();
        JsonObject s = new JsonObject();
        s.addProperty("world", world.getName());
        s.addProperty("seed", plugin.seed());
        s.addProperty("regionHalf", generator.getRegionHalf());
        s.addProperty("levelCount", generator.levelCount());
        s.addProperty("riskBlocks", generator.getRiskBlocks());
        s.addProperty("obstructionBlocks", generator.getObstructionBlocks());
        s.addProperty("uptimeSec", (System.currentTimeMillis() - startMillis) / 1000L);
        s.add("worldSpawn", pos(spawn));
        s.add("mineSpawn", pos(mineSpawn));
        s.add("mob", pos(mob));
        s.addProperty("mobValid", explorer.isValid());
        s.addProperty("mobType", explorer.getTypeName());
        s.addProperty("moving", !explorer.isAtTarget());

        // Los 4 niveles: spawn y caja (bounds) de cada uno para que el autómata
        // explore cada red con su propio límite y no se deje atraer a otras.
        JsonArray levels = new JsonArray();
        for (MineLevelSpec l : generator.getLevels()) {
            JsonObject jo = new JsonObject();
            jo.addProperty("name", l.name);
            JsonObject origin = new JsonObject();
            origin.addProperty("x", l.centerX + 0.5);
            origin.addProperty("y", l.centerY);
            origin.addProperty("z", l.centerZ + 0.5);
            jo.add("spawn", origin);
            JsonObject bounds = new JsonObject();
            bounds.addProperty("minX", l.minX());
            bounds.addProperty("maxX", l.maxX());
            bounds.addProperty("minY", l.minY);
            bounds.addProperty("maxY", l.maxY);
            bounds.addProperty("minZ", l.minZ());
            bounds.addProperty("maxZ", l.maxZ());
            jo.add("bounds", bounds);
            levels.add(jo);
        }
        s.add("mineLevels", levels);
        return s;
    }

    private JsonObject buildPosition() {
        Location l = explorer.getLocation();
        JsonObject o = pos(l);
        o.addProperty("yaw", l.getYaw());
        o.addProperty("pitch", l.getPitch());
        o.addProperty("moving", !explorer.isAtTarget());
        return o;
    }

    private JsonObject pos(Location l) {
        JsonObject o = new JsonObject();
        o.addProperty("x", l.getX());
        o.addProperty("y", l.getY());
        o.addProperty("z", l.getZ());
        return o;
    }

    // ------------------------------------------------------------------ move / teleport

    private JsonObject handleMove(JsonObject req) {
        JsonObject target = req.getAsJsonObject("target");
        double speed = req.has("speed") ? req.get("speed").getAsDouble() : 0.5;
        Location loc = new Location(world,
                target.get("x").getAsDouble(),
                target.get("y").getAsDouble(),
                target.get("z").getAsDouble());
        explorer.moveTo(loc, speed);
        JsonObject o = new JsonObject();
        o.add("target", pos(loc));
        o.addProperty("speed", speed);
        return o;
    }

    private JsonObject handleTeleport(JsonObject req) {
        JsonObject p = req.getAsJsonObject("position");
        Location loc = new Location(world,
                p.get("x").getAsDouble(),
                p.get("y").getAsDouble(),
                p.get("z").getAsDouble());
        explorer.teleport(loc);
        return pos(loc);
    }

    // ------------------------------------------------------------------ raycast (LiDAR)

    private JsonObject handleRaycast(JsonObject req) {
        JsonObject origin = req.getAsJsonObject("origin");
        double ox = origin.get("x").getAsDouble();
        double oy = origin.get("y").getAsDouble();
        double oz = origin.get("z").getAsDouble();
        double range = req.has("range") ? req.get("range").getAsDouble() : 16.0;
        int rays = req.has("rays") ? req.get("rays").getAsInt() : 0;

        Location loc = new Location(world, ox, oy, oz);
        List<Vector> dirs = new ArrayList<>();
        if (req.has("direction")) {
            JsonObject d = req.getAsJsonObject("direction");
            Vector v = new Vector(d.get("x").getAsDouble(), d.get("y").getAsDouble(), d.get("z").getAsDouble());
            if (v.lengthSquared() > 0) {
                dirs.add(v.normalize());
            }
        } else if (req.has("yaw")) {
            dirs.add(directionFromYawPitch(
                    Math.toRadians(req.get("yaw").getAsDouble()),
                    Math.toRadians(req.has("pitch") ? req.get("pitch").getAsDouble() : 0.0)));
        } else if (rays > 0) {
            dirs.addAll(fibonacciSphere(rays));
        }

        JsonArray hits = new JsonArray();
        for (Vector dir : dirs) {
            hits.add(castRay(loc, dir, range));
        }
        JsonObject o = new JsonObject();
        o.addProperty("originX", ox);
        o.addProperty("originY", oy);
        o.addProperty("originZ", oz);
        o.addProperty("count", hits.size());
        o.add("hits", hits);
        return o;
    }

    private Vector directionFromYawPitch(double yaw, double pitch) {
        double dx = -Math.sin(yaw) * Math.cos(pitch);
        double dy = -Math.sin(pitch);
        double dz = Math.cos(yaw) * Math.cos(pitch);
        return new Vector(dx, dy, dz).normalize();
    }

    private List<Vector> fibonacciSphere(int n) {
        List<Vector> dirs = new ArrayList<>(n);
        double golden = Math.PI * (3 - Math.sqrt(5));
        for (int i = 0; i < n; i++) {
            double z = 1 - (2.0 * (i + 0.5)) / n;
            double r = Math.sqrt(1 - z * z);
            double theta = golden * i;
            dirs.add(new Vector(Math.cos(theta) * r, z, Math.sin(theta) * r));
        }
        return dirs;
    }

    private JsonObject castRay(Location origin, Vector dir, double range) {
        JsonObject o = new JsonObject();
        o.addProperty("directionX", dir.getX());
        o.addProperty("directionY", dir.getY());
        o.addProperty("directionZ", dir.getZ());

        RayTraceResult res = ddaRayTrace(origin, dir, range);
        if (res == null) {
            o.addProperty("distance", range);
            o.addProperty("free", true);
            return o;
        }
        Block b = res.getHitBlock();
        double distance = origin.distance(res.getHitPosition().toLocation(world));
        if (distance > range) {
            distance = range;
        }
        o.addProperty("distance", distance);
        o.addProperty("hitX", b.getX());
        o.addProperty("hitY", b.getY());
        o.addProperty("hitZ", b.getZ());
        o.addProperty("material", b.getType().name());
        o.addProperty("risk", MineGenerator.riskLabel(b.getType()));
        o.addProperty("free", false);
        return o;
    }

    /**
     * Recorrido voxel-ray (algoritmo DDA de Amanatides & Woo): avanza bloque a
     * bloque por el grid y devuelve el primer bloque sólido (simulación de LiDAR).
     */
    private RayTraceResult ddaRayTrace(Location origin, Vector dir, double range) {
        double ox = origin.getX(), oy = origin.getY(), oz = origin.getZ();
        int bx = (int) Math.floor(ox), by = (int) Math.floor(oy), bz = (int) Math.floor(oz);

        int stepX = (int) Math.signum(dir.getX());
        int stepY = (int) Math.signum(dir.getY());
        int stepZ = (int) Math.signum(dir.getZ());

        double tDeltaX = Math.abs(1.0 / (dir.getX() == 0 ? 1e-9 : dir.getX()));
        double tDeltaY = Math.abs(1.0 / (dir.getY() == 0 ? 1e-9 : dir.getY()));
        double tDeltaZ = Math.abs(1.0 / (dir.getZ() == 0 ? 1e-9 : dir.getZ()));

        double tMaxX = stepX > 0 ? (bx + 1 - ox) / dir.getX() : dir.getX() == 0 ? Double.POSITIVE_INFINITY : (ox - bx) / -dir.getX();
        double tMaxY = stepY > 0 ? (by + 1 - oy) / dir.getY() : dir.getY() == 0 ? Double.POSITIVE_INFINITY : (oy - by) / -dir.getY();
        double tMaxZ = stepZ > 0 ? (bz + 1 - oz) / dir.getZ() : dir.getZ() == 0 ? Double.POSITIVE_INFINITY : (oz - bz) / -dir.getZ();

        int maxSteps = (int) Math.ceil(range * 2) + 8;
        for (int i = 0; i < maxSteps; i++) {
            if (tMaxX < tMaxY && tMaxX < tMaxZ) {
                bx += stepX;
                tMaxX += tDeltaX;
            } else if (tMaxY < tMaxZ) {
                by += stepY;
                tMaxY += tDeltaY;
            } else {
                bz += stepZ;
                tMaxZ += tDeltaZ;
            }
            double distance = Math.sqrt(
                    (bx + 0.5 - ox) * (bx + 0.5 - ox)
                            + (by + 0.5 - oy) * (by + 0.5 - oy)
                            + (bz + 0.5 - oz) * (bz + 0.5 - oz));
            if (distance > range) {
                return null;
            }
            Block b = world.getBlockAt(bx, by, bz);
            Material m = b.getType();
            if (m.isAir()) {
                continue;
            }
            // materiales que se ignoran (agua/aire/estructuras sin colisión)
            switch (m) {
                case CAVE_AIR, VOID_AIR, WATER, LAVA, BUBBLE_COLUMN -> {
                    continue;
                }
                default -> {
                    Vector hitPos = b.getLocation().toCenterLocation().toVector()
                            .subtract(dir.clone().multiply(0.01));
                    return new RayTraceResult(hitPos, b, null);
                }
            }
        }
        return null;
    }

    // ------------------------------------------------------------------ blocks / ground_truth

    private JsonObject handleBlocks(JsonObject req, boolean withRisk) {
        JsonObject center = req.getAsJsonObject("center");
        int cx = (int) Math.floor(center.get("x").getAsDouble());
        int cy = (int) Math.floor(center.get("y").getAsDouble());
        int cz = (int) Math.floor(center.get("z").getAsDouble());
        int radius = req.has("radius") ? req.get("radius").getAsInt() : 8;
        radius = Math.max(1, Math.min(radius, 24));

        JsonArray blocks = new JsonArray();
        for (int dx = -radius; dx <= radius; dx++) {
            for (int dy = -radius; dy <= radius; dy++) {
                for (int dz = -radius; dz <= radius; dz++) {
                    Block b = world.getBlockAt(cx + dx, cy + dy, cz + dz);
                    Material m = b.getType();
                    JsonObject blk = new JsonObject();
                    blk.addProperty("x", cx + dx);
                    blk.addProperty("y", cy + dy);
                    blk.addProperty("z", cz + dz);
                    blk.addProperty("material", m.name());
                    if (withRisk) {
                        blk.addProperty("risk", MineGenerator.riskLabel(m));
                    }
                    blocks.add(blk);
                }
            }
        }
        JsonObject o = new JsonObject();
        o.addProperty("centerX", cx);
        o.addProperty("centerY", cy);
        o.addProperty("centerZ", cz);
        o.addProperty("radius", radius);
        o.addProperty("count", blocks.size());
        o.add("blocks", blocks);
        return o;
    }

    // ------------------------------------------------------------------ reply

    private void reply(WebSocket conn, String id, JsonObject result) {
        JsonObject resp = new JsonObject();
        resp.addProperty("id", id);
        resp.addProperty("ok", true);
        resp.add("result", result);
        conn.send(resp.toString());
    }

    private void replyError(WebSocket conn, String id, String error) {
        JsonObject resp = new JsonObject();
        resp.addProperty("id", id);
        resp.addProperty("ok", false);
        resp.addProperty("error", error);
        conn.send(resp.toString());
    }
}
