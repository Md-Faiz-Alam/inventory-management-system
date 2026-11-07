from django.urls import path

from . import views

urlpatterns = [
    path("customers/", views.customer_list, name="customer-list"),
    path("customers/new/", views.customer_edit, name="customer-create"),
    path("customers/<int:pk>/", views.customer_detail, name="customer-detail"),
    path("customers/<int:pk>/edit/", views.customer_edit, name="customer-update"),
    path("customers/<int:pk>/status/", views.customer_status, name="customer-status"),
    path("sales/", views.order_list, name="order-list"),
    path("sales/new/", views.order_edit, name="order-create"),
    path("sales/<int:pk>/", views.order_detail, name="order-detail"),
    path("sales/<int:pk>/edit/", views.order_edit, name="order-update"),
    path("sales/<int:pk>/action/", views.order_action, name="order-action"),
    path("sales/<int:pk>/invoice/", views.order_invoice, name="order-invoice"),
]
