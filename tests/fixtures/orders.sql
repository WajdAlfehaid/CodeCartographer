-- Order management package, Oracle 8i era
CREATE OR REPLACE PROCEDURE calculate_order_total (
    p_order_id  IN  NUMBER,
    p_total     OUT NUMBER
) IS
    v_subtotal   NUMBER := 0;
    v_tax_rate   NUMBER := 0.08;
    v_free_ship  NUMBER := 50;
BEGIN
    SELECT SUM(line_amount) INTO v_subtotal
      FROM order_lines WHERE order_id = p_order_id;

    IF v_subtotal > v_free_ship THEN
        p_total := v_subtotal * (1 + v_tax_rate);
    ELSE
        p_total := v_subtotal * (1 + v_tax_rate) + 5.99;
    END IF;
EXCEPTION
    WHEN NO_DATA_FOUND THEN
        p_total := 0;
END;
/

CREATE OR REPLACE FUNCTION customer_tier (p_spend IN NUMBER) RETURN VARCHAR2 IS
BEGIN
    IF p_spend >= 10000 THEN
        RETURN 'PLATINUM';
    ELSIF p_spend >= 2500 THEN
        RETURN 'GOLD';
    END IF;
    RETURN 'STANDARD';
END;
/
