-- MySQL dump 10.13  Distrib 8.0.36, for Win64 (x86_64)
--
-- Host: localhost    Database: app2
-- ------------------------------------------------------
-- Server version	8.0.36

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET NAMES utf8mb4 */;
SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS=0;

DROP TABLE IF EXISTS `users`;
CREATE TABLE `users` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(120) DEFAULT NULL,
  `email` varchar(190) DEFAULT NULL,
  `age` int DEFAULT NULL,
  `balance` decimal(12,2) DEFAULT '0.00',
  `bio` text,
  `created_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_name` (`name`)
) ENGINE=InnoDB AUTO_INCREMENT=5 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

LOCK TABLES `users` WRITE;
INSERT INTO `users` VALUES (1,'علی رضایی','ali@example.com',30,1500.50,'بایو\nخط دوم','2025-01-15 10:30:00'),(2,'O''Reilly','bo@example.com',NULL,NULL,'قالب: \"نقل قول\" و بک‌اسلش \\','2025-02-20 08:00:00'),(3,'مریم احمدی','m@example.com',25,99.99,NULL,NULL),(4,'a;b','x@y.z',40,0.00,'پرانتز (تستی) و ویرگول، درون متن',NULL);
UNLOCK TABLES;

DROP TABLE IF EXISTS `orders`;
CREATE TABLE `orders` (
  `id` bigint NOT NULL,
  `user_id` int DEFAULT NULL,
  `total` decimal(14,2) DEFAULT NULL,
  `status` varchar(20) DEFAULT NULL,
  `items` text,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO `orders` VALUES (1,1,250.00,'paid','[{\"sku\": \"A1\", \"qty\": 2}]'),(2,3,80.50,'pending','تست (پرانتز)، ویرگول'),(3,2,10.00,'failed','چند\nخطی');

-- INSERT چندخطی به سبک phpMyAdmin
INSERT INTO `orders` VALUES
(4,1,5.00,'paid','row4'),
(5,3,7.25,'refunded','row5');

DROP TABLE IF EXISTS `logs`;
CREATE TABLE `logs` (
  `id` bigint NOT NULL,
  `payload` blob,
  `flag` bit(1) DEFAULT NULL,
  `ts` timestamp NULL DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO `logs` VALUES (1,_binary 0x89504E470D0A1A0A,b'1','2025-03-01 12:00:00'),(2,NULL,b'0','2025-03-02 13:30:45');

DROP TABLE IF EXISTS `v_active`;
/*!50001 DROP VIEW IF EXISTS `v_active`*/;
/*!50001 CREATE ALGORITHM=UNDEFINED DEFINER=`root`@`localhost` SQL SECURITY DEFINER VIEW `v_active` AS select `users`.`id` AS `id`,`users`.`name` AS `name` from `users` where `users`.`age` > 20 */;

/*!50003 SET SESSION SQL_MODE="" */;
DELIMITER ;;
CREATE TRIGGER `users_bu` BEFORE UPDATE ON `users` FOR EACH ROW BEGIN SET NEW.created_at = NOW(); END;;
DELIMITER ;
SET FOREIGN_KEY_CHECKS=1;

-- Dump completed on 2026-11-03 16:59:00
