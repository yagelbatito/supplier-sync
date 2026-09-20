<?php
/**
 * Per-product MINIMUM ORDER QUANTITY for the storefront.
 *
 * Paste this into the site's "Code Snippets" plugin (Snippets → Add New → PHP,
 * "Run everywhere", Activate). It reads the product meta `_min_order_qty` that
 * the ART sync writes (= 50% of the supplier's pack/minimum, floored) and:
 *   1. sets the quantity box minimum + default value to it,
 *   2. blocks adding to cart below it (with a clear message).
 *
 * Products without the meta behave normally (minimum 1). No plugin needed.
 */

// 1) Quantity input: min + starting value on the product page.
add_filter( 'woocommerce_quantity_input_args', function ( $args, $product ) {
    $min = (int) get_post_meta( $product->get_id(), '_min_order_qty', true );
    if ( $min > 1 ) {
        $args['min_value']   = $min;
        if ( empty( $args['input_value'] ) || $args['input_value'] < $min ) {
            $args['input_value'] = $min;
        }
    }
    return $args;
}, 10, 2 );

// 2) Enforce on add-to-cart (covers direct/AJAX adds that bypass the input).
add_filter( 'woocommerce_add_to_cart_validation', function ( $passed, $product_id, $qty ) {
    $min = (int) get_post_meta( $product_id, '_min_order_qty', true );
    if ( $min > 1 && $qty < $min ) {
        wc_add_notice(
            sprintf( 'מוצר זה נמכר בכמות מינימום של %d יחידות.', $min ),
            'error'
        );
        return false;
    }
    return $passed;
}, 10, 3 );

// 3) Enforce when the quantity is changed in the cart.
add_filter( 'woocommerce_update_cart_validation', function ( $passed, $cart_item_key, $values, $qty ) {
    $min = (int) get_post_meta( $values['product_id'], '_min_order_qty', true );
    if ( $min > 1 && $qty < $min ) {
        wc_add_notice(
            sprintf( 'מוצר זה נמכר בכמות מינימום של %d יחידות.', $min ),
            'error'
        );
        return false;
    }
    return $passed;
}, 10, 4 );
