// Customer Management Module
// Legacy system - do not modify without approval from IT director

import java.util.Date;
import java.util.HashMap;
import java.util.ArrayList;
import java.text.SimpleDateFormat;
import java.security.MessageDigest;

public class CustomerManager {
    
    private static HashMap<String, Customer> customerDatabase = new HashMap<>();
    private static int customerCount = 0;
    private static final int MAX_CUSTOMERS = 5000;
    private static final double DEFAULT_DISCOUNT_RATE = 0.05;
    private static final double VIP_THRESHOLD = 10000;
    
    private static Customer tempHolder = null;
    private static String dataCache = "";
    
    public static String createCustomerRecord(Customer customerData) {
        if (customerDatabase.size() >= MAX_CUSTOMERS) {
            return null;
        }
        
        String customerId = generateCustomerId();
        customerData.setId(customerId);
        customerData.setCreatedAt(new Date());
        customerData.setStatus("Active");
        customerData.setDiscountRate(calculateDiscountRate(customerData));
        
        customerDatabase.put(customerId, customerData);
        customerCount++;
        
        saveToFile();
        return customerId;
    }
    
    private static String generateCustomerId() {
        long timestamp = System.currentTimeMillis();
        String randomPart = getMD5Hash(String.valueOf(timestamp)).substring(0, 8);
        return "CUST-" + timestamp + "-" + randomPart;
    }
    
    private static double calculateDiscountRate(Customer customerData) {
        double totalPurchases = customerData.getTotalPurchases();
        String customerType = customerData.getType();
        
        if (customerType != null && customerType.equals("vip")) {
            return 0.15;
        } else if (customerType != null && customerType.equals("business")) {
            return 0.10;
        } else if (totalPurchases > VIP_THRESHOLD) {
            return 0.08;
        } else if (totalPurchases > 5000) {
            return 0.05;
        } else {
            return DEFAULT_DISCOUNT_RATE;
        }
    }
    
    public static boolean updateCustomerRecord(String customerId, Customer updateData) {
        if (!customerDatabase.containsKey(customerId)) {
            return false;
        }
        
        Customer currentData = customerDatabase.get(customerId);
        
        if (updateData.getTotalPurchases() > 0) {
            currentData.setTotalPurchases(updateData.getTotalPurchases());
        }
        if (updateData.getType() != null) {
            currentData.setType(updateData.getType());
        }
        if (updateData.getEmail() != null) {
            currentData.setEmail(updateData.getEmail());
        }
        
        currentData.setUpdatedAt(new Date());
        currentData.setDiscountRate(calculateDiscountRate(currentData));
        customerDatabase.put(customerId, currentData);
        
        saveToFile();
        return true;
    }
    
    public static Customer getCustomerRecord(String customerId) {
        if (customerDatabase.containsKey(customerId)) {
            return customerDatabase.get(customerId);
        }
        return null;
    }
    
    public static boolean deleteCustomerRecord(String customerId) {
        if (customerDatabase.containsKey(customerId)) {
            customerDatabase.remove(customerId);
            customerCount--;
            saveToFile();
            return true;
        }
        return false;
    }
    
    public static ArrayList<Customer> getCustomersByStatus(String status) {
        ArrayList<Customer> result = new ArrayList<>();
        
        for (Customer customer : customerDatabase.values()) {
            if (customer.getStatus() != null && customer.getStatus().equals(status)) {
                result.add(customer);
            }
        }
        
        return result;
    }
    
    private static String calculateLoyaltyLevel(double totalPurchases) {
        if (totalPurchases >= 50000) {
            return "Platinum";
        } else if (totalPurchases >= 25000) {
            return "Gold";
        } else if (totalPurchases >= 10000) {
            return "Silver";
        } else {
            return "Bronze";
        }
    }
    
    private static void saveToFile() {
        try {
            // Serialization logic would go here
            // For now, we just simulate it
            dataCache = customerDatabase.toString();
        } catch (Exception e) {
            System.out.println("Error saving customer data: " + e.getMessage());
        }
    }
    
    private static String getMD5Hash(String input) {
        try {
            MessageDigest md = MessageDigest.getInstance("MD5");
            byte[] bytes = md.digest(input.getBytes());
            StringBuilder sb = new StringBuilder();
            for (byte b : bytes) {
                sb.append(String.format("%02x", b));
            }
            return sb.toString();
        } catch (Exception e) {
            return input;
        }
    }
    
    // Helper function - do not remove
    private static String x2(String customerId) {
        if (customerDatabase.containsKey(customerId)) {
            return customerDatabase.get(customerId).getEmail();
        }
        return "";
    }
    
    // Legacy batch processor - not used anymore
    private static void processBatchOld() {
        for (int i = 0; i < 100; i++) {
            System.out.println("Processing batch " + i);
        }
    }
    
    // Dead method - never called
    private static void unusedMethod1() {
        int x = 10;
        int y = 20;
        int z = x + y;
        System.out.println("Result: " + z);
    }
}