package com.doccompare;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

record ChatAttachment(String name, byte[] data) {
    static List<ChatAttachment> read(List<MultipartFile> files) throws IOException {
        if (files == null) {
            return List.of();
        }
        if (files.size() > 3) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "每条消息最多添加 3 个附件。");
        }
        if (files.stream().anyMatch(MultipartFile::isEmpty)
                || files.stream().mapToLong(MultipartFile::getSize).sum() > 10 * 1024 * 1024) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "附件不能为空，所有附件总计最多 10 MB。");
        }
        var attachments = new ArrayList<ChatAttachment>();
        for (MultipartFile file : files) {
            String name = file.getOriginalFilename();
            name = name == null ? "" : name.replace('\\', '/');
            name = name.substring(name.lastIndexOf('/') + 1).strip();
            if (name.isEmpty() || name.length() > 255 || name.chars().anyMatch(Character::isISOControl)) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "附件文件名无效。");
            }
            attachments.add(new ChatAttachment(name, file.getBytes()));
        }
        return List.copyOf(attachments);
    }

    static String fingerprint(List<ChatAttachment> files) {
        if (files.isEmpty()) {
            return null;
        }
        try {
            var digest = MessageDigest.getInstance("SHA-256");
            for (ChatAttachment file : files) {
                byte[] name = file.name().getBytes(StandardCharsets.UTF_8);
                digest.update(ByteBuffer.allocate(8).putInt(name.length).putInt(file.data().length).array());
                digest.update(name);
                digest.update(file.data());
            }
            return HexFormat.of().formatHex(digest.digest());
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 unavailable", error);
        }
    }
}
