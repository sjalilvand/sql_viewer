-- MySQL dump 10.13 - نمونه حالت‌های خاص
/*!40101 SET NAMES utf8mb4 */;

DROP TABLE IF EXISTS `t1`;
CREATE TABLE `t1` (
  `id` int NOT NULL,
  `txt` varchar(200) DEFAULT NULL,
  `note` text,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- escape با بک‌اسلش (سبک mysqldump) + INSERT با ستون‌بندی
INSERT INTO `t1` (`id`, `txt`, `note`) VALUES (1,'D\'Angelo','back\\slash'),(2,'quote\' and \'more','tab\there'),(3,'NULL درون رشته','نوشته NULL'),(4,'multi\\nline','x');
INSERT IGNORE INTO `t1` (`id`, `txt`) VALUES (4,'دوباره — باید نادیده گرفته شود');
REPLACE INTO `t1` (`id`, `txt`) VALUES (4,'جایگزین شده');

-- رشته حاوی ( و ) و , و ؛ داخل متن
INSERT INTO `t1` (`id`,`note`) VALUES (5,'(تست) , کاما ; نقطه‌ویرگول و `بک‌تیک`');

-- جدول بدون PRIMARY KEY و با DEFAULT عددی
DROP TABLE IF EXISTS `t2`;
CREATE TABLE `t2` (
  `a` int DEFAULT 0,
  `b` double DEFAULT NULL,
  `c` varchar(10) DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO `t2` VALUES (-5,1.25e-3,'neg'),(0,1e10,'exp'),(7,NULL,'');

-- جدول با نام شامل نقطه در بکاپ (schema-qualified)
DROP TABLE IF EXISTS `app2`.`t3`;
CREATE TABLE `app2`.`t3` (
  `x` int DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO `app2`.`t3` VALUES (10),(20);
