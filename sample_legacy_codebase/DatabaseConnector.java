// Database Connector Module
// Handles all database operations

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;
import java.sql.PreparedStatement;
import java.util.ArrayList;
import java.util.List;

public class DatabaseConnector {
    
    private static Connection connection = null;
    private static final String DB_URL = "jdbc:mysql://localhost:3306/legacy_system";
    private static final String DB_USER = "admin";
    private static final String DB_PASSWORD = "changeme123";
    private static final int MAX_POOL_SIZE = 10;
    private static List<Connection> connectionPool = new ArrayList<>();
    
    public static Connection connectDatabase() {
        if (connectionPool.size() >= MAX_POOL_SIZE) {
            return null;
        }
        
        try {
            Connection conn = DriverManager.getConnection(DB_URL, DB_USER, DB_PASSWORD);
            connectionPool.add(conn);
            return conn;
        } catch (Exception e) {
            System.out.println("Database connection error: " + e.getMessage());
            return null;
        }
    }
    
    public static void disconnectDatabase(Connection conn) {
        if (connectionPool.contains(conn)) {
            connectionPool.remove(conn);
            try {
                conn.close();
            } catch (Exception e) {
                System.out.println("Error closing connection: " + e.getMessage());
            }
        }
    }
    
    public static ResultSet executeQuery(String query, Object[] params) {
        Connection conn = connectDatabase();
        
        if (conn == null) {
            return null;
        }
        
        try {
            PreparedStatement stmt = conn.prepareStatement(query);
            
            if (params != null) {
                for (int i = 0; i < params.length; i++) {
                    stmt.setObject(i + 1, params[i]);
                }
            }
            
            return stmt.executeQuery();
        } catch (Exception e) {
            System.out.println("Query error: " + e.getMessage());
            return null;
        }
    }
    
    public static int executeInsert(String table, Object[] values) {
        Connection conn = connectDatabase();
        
        if (conn == null) {
            return -1;
        }
        
        try {
            StringBuilder placeholders = new StringBuilder();
            for (int i = 0; i < values.length; i++) {
                if (i > 0) placeholders.append(",");
                placeholders.append("?");
            }
            
            String query = "INSERT INTO " + table + " VALUES (" + placeholders.toString() + ")";
            PreparedStatement stmt = conn.prepareStatement(query);
            
            for (int i = 0; i < values.length; i++) {
                stmt.setObject(i + 1, values[i]);
            }
            
            return stmt.executeUpdate();
        } catch (Exception e) {
            System.out.println("Insert error: " + e.getMessage());
            return -1;
        } finally {
            disconnectDatabase(conn);
        }
    }
    
    public static boolean checkDatabaseHealth() {
        Connection conn = connectDatabase();
        
        if (conn == null) {
            return false;
        }
        
        try {
            Statement stmt = conn.createStatement();
            ResultSet rs = stmt.executeQuery("SELECT 1");
            boolean healthy = rs.next();
            disconnectDatabase(conn);
            return healthy;
        } catch (Exception e) {
            System.out.println("Health check error: " + e.getMessage());
            disconnectDatabase(conn);
            return false;
        }
    }
    
    // Dead method - old connection logic
    private static Connection getOldConnection() {
        try {
            return DriverManager.getConnection(DB_URL, "old_admin", "old_password");
        } catch (Exception e) {
            return null;
        }
    }
}