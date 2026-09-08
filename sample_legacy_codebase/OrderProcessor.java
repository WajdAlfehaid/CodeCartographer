// Order Processing Module
// Handles customer orders and inventory

import java.util.Date;
import java.util.HashMap;
import java.util.ArrayList;
import java.text.SimpleDateFormat;

public class OrderProcessor {
    
    private static HashMap<String, Order> orderDatabase = new HashMap<>();
    private static int orderCount = 0;
    private static final int MAX_ORDER_ITEMS = 50;
    private static final double MIN_ORDER_VALUE = 10.0;
    private static final double MAX_ORDER_VALUE = 100000.0;
    private static final double TAX_RATE = 0.08;
    private static final double SHIPPING_RATE = 5.99;
    private static final double FREE_SHIPPING_THRESHOLD = 50.0;
    
    public static String createOrder(String customerId, ArrayList<OrderItem> items) {
        if (items == null || items.size() == 0 || items.size() > MAX_ORDER_ITEMS) {
            return null;
        }
        
        String orderId = "ORD-" + System.currentTimeMillis() + "-" + customerId.substring(0, 8);
        
        Order orderData = new Order();
        orderData.setOrderId(orderId);
        orderData.setCustomerId(customerId);
        orderData.setItems(items);
        orderData.setOrderDate(new Date());
        orderData.setStatus("Pending");
        
        double subtotal = calculateSubtotal(items);
        orderData.setSubtotal(subtotal);
        orderData.setTax(subtotal * TAX_RATE);
        orderData.setShipping(calculateShipping(subtotal));
        orderData.setTotal(subtotal + orderData.getTax() + orderData.getShipping());
        
        if (orderData.getTotal() < MIN_ORDER_VALUE) {
            return null;
        }
        
        if (orderData.getTotal() > MAX_ORDER_VALUE) {
            return null;
        }
        
        orderDatabase.put(orderId, orderData);
        orderCount++;
        
        saveOrders();
        return orderId;
    }
    
    private static double calculateSubtotal(ArrayList<OrderItem> items) {
        double total = 0;
        
        for (OrderItem item : items) {
            if (item.getPrice() > 0 && item.getQuantity() > 0) {
                total += item.getPrice() * item.getQuantity();
            } else if (item.getPrice() > 0) {
                total += item.getPrice();
            }
        }
        
        return total;
    }
    
    private static double calculateShipping(double subtotal) {
        if (subtotal >= FREE_SHIPPING_THRESHOLD) {
            return 0;
        }
        return SHIPPING_RATE;
    }
    
    public static boolean updateOrderStatus(String orderId, String newStatus) {
        String[] validStatuses = {"Pending", "Processing", "Shipped", "Delivered", "Cancelled"};
        
        boolean isValid = false;
        for (String status : validStatuses) {
            if (status.equals(newStatus)) {
                isValid = true;
                break;
            }
        }
        
        if (!isValid) {
            return false;
        }
        
        if (orderDatabase.containsKey(orderId)) {
            Order order = orderDatabase.get(orderId);
            order.setStatus(newStatus);
            order.setUpdatedAt(new Date());
            saveOrders();
            return true;
        }
        
        return false;
    }
    
    public static Order getOrder(String orderId) {
        if (orderDatabase.containsKey(orderId)) {
            return orderDatabase.get(orderId);
        }
        return null;
    }
    
    public static ArrayList<Order> getOrdersByCustomer(String customerId) {
        ArrayList<Order> result = new ArrayList<>();
        
        for (Order order : orderDatabase.values()) {
            if (order.getCustomerId() != null && order.getCustomerId().equals(customerId)) {
                result.add(order);
            }
        }
        
        return result;
    }
    
    private static void saveOrders() {
        try {
            // Serialization logic
            System.out.println("Orders saved");
        } catch (Exception e) {
            System.out.println("Error saving orders: " + e.getMessage());
        }
    }
    
    // Dead method - never called
    private static void oldOrderProcessor() {
        int x = 0;
        for (int i = 0; i < 1000; i++) {
            x += i;
        }
    }
}